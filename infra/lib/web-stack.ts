import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as cdk from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import type { Construct } from 'constructs';
import type { EntryStack } from './entry-stack.js';
import type { WatchStack } from './watch-stack.js';

const currentDir = path.dirname(fileURLToPath(import.meta.url));

export interface WebStackProps extends cdk.StackProps {
  readonly entry: EntryStack;
  // /admin* の中継先（利用量ページの関数URL）
  readonly watch: WatchStack;
  // us-east-1 にある WAF と証明書（infra/scripts/domain.py が infra/.edge.json に書く）。無ければ付けない
  readonly edge?: { webAclArn?: string; certificateArn?: string; domain?: string };
  // URL から作ったスライドの置き場（/s/<ジョブ>/ をビューアとして配る）
  // 別スタックのバケットに OAC の許可を足すと循環するので、名前で取り込み、許可は MinorunSlides 側に置く
  readonly slidesBucketName: string;
  // 資料から作った解説動画の置き場（/v/<ジョブ>/ を配る。許可は MinorunVideo 側）
  readonly videosBucketName?: string;
}

// 画面は S3、/api は入口の Gateway へ。ブラウザが見るドメインは1つにする。
export class WebStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);

    const bucket = new s3.Bucket(this, 'Site', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // /api/chat → Gateway の /<ターゲット名>/invocations へ書き換える
    const rewrite = new cloudfront.Function(this, 'ApiRewrite', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event){var r=event.request;if(r.uri==='/api/chat'){r.uri='/minorun/invocations';}return r;}",
      ),
    });

    // /s/<ジョブ>/ と /s/<ジョブ> を index.html へ（S3 は既定のファイルを持たないため）
    const slidesIndex = new cloudfront.Function(this, 'SlidesIndex', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event){var r=event.request;if(/^\\/s\\/[A-Za-z0-9_-]+\\/?$/.test(r.uri)){if(!r.uri.endsWith('/')){return{statusCode:301,statusDescription:'Moved',headers:{location:{value:r.uri+'/'}}};}r.uri+='index.html';}return r;}",
      ),
    });

    // /v/<ジョブ>/ と /v/<ジョブ> を、動画のビューア（画面と同じバケットの v.html）へ
    const videoIndex = new cloudfront.Function(this, 'VideoIndex', {
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(
        "function handler(event){var r=event.request;if(/^\\/v\\/[A-Za-z0-9_-]+\\/?$/.test(r.uri)){if(!r.uri.endsWith('/')){return{statusCode:301,statusDescription:'Moved',headers:{location:{value:r.uri+'/'}}};}r.uri+='index.html';}return r;}",
      ),
    });

    const edge = props.edge ?? {};
    const custom = edge.certificateArn && edge.domain
      ? { domainNames: [edge.domain], certificate: acm.Certificate.fromCertificateArn(this, 'Certificate', edge.certificateArn) }
      : {};
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultRootObject: 'index.html',
      webAclId: edge.webAclArn,
      ...custom,
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(bucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.HttpOrigin(props.entry.gatewayDomain, {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            readTimeout: cdk.Duration.seconds(60),
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          functionAssociations: [{ function: rewrite, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        },
        // 作ったスライドのビューア・画像・進み具合（status.json は no-cache なので最短1秒で取り直す）
        '/s/*': {
          origin: origins.S3BucketOrigin.withOriginAccessControl(s3.Bucket.fromBucketName(this, 'SlidesBucket', props.slidesBucketName)),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: new cloudfront.CachePolicy(this, 'SlidesCache', {
            minTtl: cdk.Duration.seconds(0), defaultTtl: cdk.Duration.minutes(5), maxTtl: cdk.Duration.days(365),
            enableAcceptEncodingGzip: true, enableAcceptEncodingBrotli: true,
          }),
          functionAssociations: [{ function: slidesIndex, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        },
        ...(props.videosBucketName ? {
          // 作った解説動画・表紙・字幕・進み具合（status.json は no-cache）
          '/v/*': {
            origin: origins.S3BucketOrigin.withOriginAccessControl(s3.Bucket.fromBucketName(this, 'VideosBucket', props.videosBucketName)),
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cachePolicy: new cloudfront.CachePolicy(this, 'VideosCache', {
              minTtl: cdk.Duration.seconds(0), defaultTtl: cdk.Duration.minutes(5), maxTtl: cdk.Duration.days(365),
              enableAcceptEncodingGzip: true, enableAcceptEncodingBrotli: true,
            }),
            functionAssociations: [{ function: videoIndex, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
          },
        } : {}),
        // 監視ページ（ログインは Lambda 側）。cookie とクエリをそのまま渡し、キャッシュしない
        '/admin*': {
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          origin: new origins.HttpOrigin(cdk.Fn.select(2, cdk.Fn.split('/', props.watch.usagePageUrl.url)), {
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            readTimeout: cdk.Duration.seconds(30),
          }),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        },
      },
    });

    // 画面は web/ を Vite で組んだもの（`npm run web:build` を先に流す）。
    // 名前に中身のハッシュが入る assets/ は長く、index.html などは毎回取り直させる
    const dist = path.join(currentDir, '../../web/dist');
    const assets = new s3deploy.BucketDeployment(this, 'DeployAssets', {
      // 長く持たせるのは assets/ だけ。ogp.jpg・アイコン・robots.txt に immutable が付くと、
      // X のカードや検索エンジンが差し替えを取り直せない（2026-10-25 に ogp.jpg で踏んだ）
      sources: [s3deploy.Source.asset(path.join(dist, 'assets'))],
      destinationBucket: bucket,
      destinationKeyPrefix: 'assets/',
      prune: false,
      cacheControl: [s3deploy.CacheControl.fromString('public, max-age=31536000, immutable')],
    });
    const site = new s3deploy.BucketDeployment(this, 'DeploySite', {
      sources: [s3deploy.Source.asset(dist, { exclude: ['assets/**'] })],
      destinationBucket: bucket,
      prune: false,
      cacheControl: [s3deploy.CacheControl.fromString('no-cache')],
      distribution,
    });
    site.node.addDependency(assets);

    new cdk.CfnOutput(this, 'Url', { value: `https://${edge.domain ?? distribution.distributionDomainName}` });
    new cdk.CfnOutput(this, 'DistributionDomain', { value: distribution.distributionDomainName });
    new cdk.CfnOutput(this, 'DistributionId', { value: distribution.distributionId });
  }
}
