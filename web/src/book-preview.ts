import type { Surprise } from './surprises';

// 出版社・Amazonの商品ページで確認した書影と紙書籍のリンク。
const books = [
  {
    id: 'claudecode', title: 'みのるん式の仕事術',
    name: '［みのるん式］ビジネスパーソンのためのClaude Code仕事術',
    mention: /Claude\s*Code\s*仕事術|みのるん式[^。\n]{0,24}(?:本|書籍|仕事術)/i,
    asin: '4297159392', publisher: 'https://gihyo.jp/book/2026/978-4-297-15939-9',
    image: 'https://gihyo.jp/assets/images/cover/2026/thumb/TH800_9784297159399.jpg',
  },
  {
    id: 'agentcore', title: 'AgentCore 実践入門',
    name: 'Amazon Bedrock AgentCore 実践入門 Strands Agentsで構築するAIエージェント',
    mention: /AgentCore\s*(?:実践入門|本)|AgentCoreの(?:本|書籍)/i,
    asin: '4815641234', publisher: 'https://www.sbcr.jp/product/4815641234/',
    image: 'https://m.media-amazon.com/images/I/81Eb-KuRVxL._SY522_.jpg',
  },
];

export function bookGift(answer: string, sources: string[]): Surprise | undefined {
  const plain = answer.replace(/\*|_/g, '');
  const matches = books.filter(book => book.mention.test(plain) || sources.some(source => {
    try {
      const url = new URL(source);
      if (url.protocol !== 'https:' || url.username || url.password || url.port) return false;
      if (['amazon.co.jp', 'www.amazon.co.jp'].includes(url.hostname)) {
        return new RegExp(`/(?:dp|gp/product|o/ASIN)/${book.asin}(?:/|$)`).test(url.pathname);
      }
      return url.origin + url.pathname.replace(/\/$/, '') === book.publisher.replace(/\/$/, '');
    } catch { return false; }
  }));
  // 複数冊を紹介した場合は、本人が推している最新刊を先にする。
  const book = matches[0];
  if (!book) return;
  return { id: 'preview:book:' + book.id, title: book.title, text: book.name,
    image: book.image, source: 'https://www.amazon.co.jp/dp/' + book.asin,
    sourceLabel: 'Amazonで見る', preview: true, pose: 'pc' };
}
