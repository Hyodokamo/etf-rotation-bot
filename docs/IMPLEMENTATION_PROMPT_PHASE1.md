**# Claude Code 実装プロンプト**



**あなたは、Pythonによる個人投資レビューBotを実装するシニアソフトウェアエンジニアです。**



**以下の要件に基づいて、`AI監査型ETFローテーションBot Phase 1` を実装してください。**



**重要：**

**- 今回は Phase 1 のみ実装する**

**- AI監査は実装しない**

**- 自動売買は実装しない**

**- 楽天証券連携は実装しない**

**- NISA内ローテーションは実装しない**

**- ETFの価格取得、指標計算、スコアリング、配分計算、リスク制約、ターンオーバー制約、Markdownレポート生成、Slack投稿、ログ保存までを実装する**



**---**



**## 1. プロジェクト名**



**`etf-rotation-bot`**



**目的は、ETFユニバース25本を月次で評価し、モメンタム・トレンド・ボラティリティ・相関・リスク制約を考慮した推奨配分を作成し、MarkdownレポートとSlack投稿を生成すること。**



**---**



**## 2. 実装対象**



**### 対象ETFユニバース**



**以下25本を初期実装対象にする。**



**| asset\_id | ticker | display\_name | category | include\_stage |**

**|---|---|---|---|---|**

**| sp500 | VOO | S\&P500 | core\_equity | production |**

**| ex\_us\_equity | VXUS | 米国外株式 | core\_equity | production |**

**| topix\_jpy | 1306.T | TOPIX | core\_equity | production |**

**| emerging | VWO | 新興国全体 | core\_equity | production |**

**| nasdaq100 | QQQM | NASDAQ100 | growth\_equity | production |**

**| us\_semiconductor | SMH | 米国半導体 | theme\_equity | production |**

**| india\_equity | INDA | インド株 | emerging\_equity | production |**

**| us\_long\_bond | TLT | 米国長期国債 | bond | production |**

**| us\_mid\_bond | IEF | 米国中期国債 | bond | production |**

**| us\_short\_cash | SGOV | 米国超短期国債 | cash\_like | production |**

**| gold\_usd | GLDM | ゴールド | commodity | production |**

**| j\_reit | 1343.T | J-REIT | reit | production |**

**| us\_reit | VNQ | 米国REIT | reit | production |**

**| us\_small\_cap | IWM | 米国小型株 | small\_cap | production |**

**| healthcare | XLV | ヘルスケア | sector | production |**

**| energy | XLE | エネルギー | sector | production |**

**| industrials | XLI | 資本財 | sector | production |**

**| financials | XLF | 金融 | sector | production |**

**| us\_dollar | UUP | 米ドル | fx | production |**

**| japan\_bond | 2561.T | 日本国債 | bond | production |**

**| us\_value | VTV | 米国バリュー | factor | production |**

**| us\_momentum | MTUM | 米国モメンタム | factor | production |**

**| gold\_jpy | 1540.T | 国内金 | commodity | production |**

**| us\_aggregate\_bond | BND | 米国総合債券 | bond | production |**

**| commodities | DBC | コモディティ総合 | commodity | production |**



**監視候補は今回は実装対象外。**  

**ただし、将来拡張できるように `include\_stage: watch` を扱える設計にはしておく。**



**---**



**## 3. 技術スタック**



**Pythonで実装する。**



**使用ライブラリ：**



**- pandas**

**- numpy**

**- yfinance**

**- PyYAML**

**- pydantic**

**- python-dotenv**

**- requests**

**- pytest**



**Slack投稿はIncoming Webhook方式でよい。**  

**`SLACK\_WEBHOOK\_URL` が未設定の場合は、Slack投稿をスキップし、ログに warning を出す。**



**---**



**## 4. ディレクトリ構成**



**以下の構成で作成する。**



**```text**

**etf-rotation-bot/**

&#x20; **config.yaml**

&#x20; **.env.example**

&#x20; **.gitignore**

&#x20; **README.md**

&#x20; **requirements.txt**

&#x20; **main.py**



&#x20; **src/**

&#x20;   **\_\_init\_\_.py**

&#x20;   **config\_loader.py**

&#x20;   **data\_fetcher.py**

&#x20;   **data\_quality.py**

&#x20;   **indicators.py**

&#x20;   **scoring.py**

&#x20;   **correlation.py**

&#x20;   **allocation.py**

&#x20;   **risk\_gate.py**

&#x20;   **turnover.py**

&#x20;   **report\_builder.py**

&#x20;   **slack\_client.py**

&#x20;   **portfolio\_state.py**

&#x20;   **logger.py**



&#x20; **outputs/**

&#x20;   **.gitkeep**



&#x20; **tests/**

&#x20;   **test\_indicators.py**

&#x20;   **test\_scoring.py**

&#x20;   **test\_allocation.py**

&#x20;   **test\_risk\_gate.py**

&#x20;   **test\_turnover.py**

