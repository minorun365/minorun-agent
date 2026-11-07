import type { Surprise } from './surprises';

// 出版社・Amazonの商品ページで確認した書影と紙書籍のリンク。
export const books = [
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
  // 以下は11/7（土）に出版社とAmazonの商品画像で表紙を確認して足した。
  {
    id: 'mastra', title: 'Mastra 実践入門',
    name: 'MastraによるAIエージェント開発・運用［実践入門］',
    mention: /Mastra(?:による|の)\s*(?:AIエージェント開発|本|書籍)/i,
    asin: '4297157667', publisher: 'https://gihyo.jp/book/2026/978-4-297-15766-1',
    image: 'https://gihyo.jp/assets/images/cover/2026/thumb/TH800_9784297157661.jpg',
  },
  {
    id: 'llm', title: '［入門］LLMアプリ開発',
    name: '［入門］LLMアプリ開発',
    mention: /[［\[]入門[］\]]\s*LLMアプリ開発/,
    asin: '4297154706', publisher: 'https://gihyo.jp/book/2026/978-4-297-15470-7',
    image: 'https://gihyo.jp/assets/images/cover/2026/thumb/TH800_9784297154707.jpg',
  },
  {
    id: 'agent', title: 'AIエージェント開発/運用入門',
    name: 'AIエージェント開発/運用入門［生成AI深掘りガイド］',
    mention: /AIエージェント開発\s*[\/／]\s*運用入門/,
    asin: '4815636605', publisher: 'https://www.sbcr.jp/product/4815636605/',
    image: 'https://m.media-amazon.com/images/P/4815636605.09.LZZZZZZZ.jpg',
  },
  {
    id: 'mcp', title: 'やさしいMCP入門',
    name: 'やさしいMCP入門',
    mention: /やさしいMCP入門/,
    asin: '4798075736', publisher: 'https://www.shuwasystem.co.jp/book/9784798075730.html',
    image: 'https://m.media-amazon.com/images/P/4798075736.09.LZZZZZZZ.jpg',
  },
  {
    id: 'bedrock', title: 'Bedrock 生成AIアプリ開発入門',
    name: 'Amazon Bedrock 生成AIアプリ開発入門［AWS深掘りガイド］',
    mention: /Bedrock\s*生成AIアプリ開発入門/i,
    asin: '4815626448', publisher: 'https://www.sbcr.jp/product/4815626448/',
    image: 'https://m.media-amazon.com/images/P/4815626448.09.LZZZZZZZ.jpg',
  },
];
export type Book = typeof books[number];
// preview は回答で紹介した本（表示回数の上限の外）。話題で選ぶ付箋は false にして上限と間隔に数える。
export function bookSurprise(book: Book, preview = true): Surprise {
  return { id: (preview ? 'preview:book:' : 'book-') + book.id, title: book.title, text: book.name,
    image: book.image, source: 'https://www.amazon.co.jp/dp/' + book.asin,
    sourceLabel: 'Amazonで見る', preview, cover: true, pose: 'pc' };
}

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
  return bookSurprise(book);
}
