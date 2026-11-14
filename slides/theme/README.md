# スライドのテーマ

スライド工房は、このフォルダの `minorun-glass.css` を Marp のテーマとして読み込みます（`slides/handler.py` の `THEME`）。公開版にはテーマの CSS を入れていないので、使うときは各自で用意してここに置きます。

- CSS の先頭に `/* @theme minorun-glass */` を書く（デッキのフロントマターがこの名前を指定するため）
- 指示文（`slides/handler.py` の `RULES`）が使うクラスを持たせる：表紙 `section.top`、中扉 `section.crosshead`、締め `section.end`、手順 `.steps`、流れ図 `.flow`、依頼文 `.prompt`、要点 `p.point`
- 書体はコンテナに入れた Zen Kaku Gothic New（見出し）・Noto Sans JP（本文）・Inter を指定する（`slides/Dockerfile`）。外部の書体を `@import` で読むと、書き出し時の通信を止めているため反映されない
