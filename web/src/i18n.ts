import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { adminTranslations } from './adminTranslations'
import { pageTranslations } from './pageTranslations'

const supportedLanguages = ['vi', 'en', 'ja'] as const

const resources = {
  vi: {
    translation: {
      'nav.features': 'Tính năng',
      'nav.platform': 'Giải pháp',
      'nav.contact': 'Kết nối',
      'nav.landingNavigation': 'Điều hướng trang giới thiệu',
      'nav.workspaceNavigation': 'Điều hướng workspace',
      'nav.dashboard': 'Bảng điều khiển',
      'nav.workspaces': 'Không gian làm việc',
      'nav.intelligence': 'Trợ lý tài liệu',
      'nav.language': 'Ngôn ngữ',
      'nav.home': 'Về trang chủ',
      'nav.enterWorkspace': 'Vào workspace',
      'nav.signIn': 'Đăng nhập',
      'nav.signOut': 'Đăng xuất',
      'landing.eyebrow': 'TRỢ LÝ TÀI LIỆU AI',
      'landing.headline': 'Tri thức của doanh nghiệp,',
      'landing.headlineAccent': 'sẵn sàng khi bạn cần',
      'landing.description':
        'Quản lý, tìm lại và hỏi đáp trên kho tài liệu của bạn trong một không gian làm việc rõ ràng, có nguồn tham khảo và bảo vệ phiên truy cập.',
      'landing.primaryAction': 'Bắt đầu ngay',
      'landing.secondaryAction': 'Xem workspace',
      'landing.productFrameLabel': 'Dữ liệu workspace trực tiếp từ backend',
      'landing.product.workspaceName': 'Main workspace',
      'landing.product.overview': 'Tổng quan',
      'landing.product.documents': 'Tài liệu',
      'landing.product.insights': 'Trợ lý AI',
      'landing.product.liveData': 'DỮ LIỆU TRỰC TIẾP',
      'landing.product.overviewTitle': 'Tổng quan workspace',
      'landing.product.workspaceCount': 'Số workspace: {{count}}',
      'landing.product.documentCount': 'Số tài liệu: {{count}}',
      'landing.product.empty': 'Backend chưa có workspace.',
      'landing.sectionLabel': 'NỀN TẢNG TÀI LIỆU AI',
      'landing.featuresTitle': 'Làm việc với tài liệu nhanh hơn',
      'landing.featuresDescription':
        'Tận dụng AI để tìm thông tin, kiểm tra nguồn và bảo vệ luồng làm việc mà không bắt người dùng đọc thuật toán phía sau.',
      'landing.features.semantic-search.title': 'Tìm theo ý bạn',
      'landing.features.semantic-search.description':
        'Đặt câu hỏi bằng ngôn ngữ tự nhiên và nhận câu trả lời kèm nguồn tham khảo rõ ràng.',
      'landing.features.hybrid-retrieval.title': 'Tìm lại đúng nguồn',
      'landing.features.hybrid-retrieval.description':
        'Kết hợp từ khóa, nội dung và mức độ phù hợp để đưa tài liệu liên quan lên trước.',
      'landing.features.analysis.title': 'Phân tích có nguồn',
      'landing.features.analysis.description':
        'Câu trả lời luôn đi cùng trích dẫn để bạn kiểm tra lại tài liệu gốc khi cần.',
      'landing.features.security.title': 'Phiên làm việc an toàn',
      'landing.features.security.description':
        'Mỗi lượt thao tác được bảo vệ bằng phiên đăng nhập, mã chống gửi lại và kiểm tra tính toàn vẹn.',
      'landing.securityPoints.session': 'Phiên đăng nhập được bảo vệ',
      'landing.securityPoints.replay': 'Chống gửi lại yêu cầu',
      'landing.statsLabel': 'Số liệu trực tiếp từ backend',
      'landing.stats.workspaces': 'Workspace từ BE',
      'landing.stats.documents': 'Tài liệu khả dụng',
      'landing.stats.precision': 'Khớp nội dung',
      'landing.stats.suggestionQuality': 'Gợi ý phù hợp',
      'landing.cta.title': 'Sẵn sàng làm việc với kho tri thức của bạn?',
      'landing.cta.description':
        'Chọn workspace được backend phát hiện và bắt đầu quản lý tài liệu.',
      'landing.footer.security': 'Phiên truy cập được bảo vệ',
      'state.loadingTitle': 'Đang kết nối backend',
      'state.loadingBody': 'Đang tải cấu hình và workspace qua API đã ký.',
      'state.errorTitle': 'Không thể tải dữ liệu từ backend',
      'state.retry': 'Thử lại',
      'status.on': 'BẬT',
      'status.off': 'TẮT',
    },
  },
  en: {
    translation: {
      'nav.features': 'Features',
      'nav.platform': 'Platform',
      'nav.contact': 'Contact',
      'nav.landingNavigation': 'Landing page navigation',
      'nav.workspaceNavigation': 'Workspace navigation',
      'nav.dashboard': 'Dashboard',
      'nav.workspaces': 'Workspaces',
      'nav.intelligence': 'Document AI',
      'nav.language': 'Language',
      'nav.home': 'Back home',
      'nav.enterWorkspace': 'Enter workspace',
      'nav.signIn': 'Sign in',
      'nav.signOut': 'Sign out',
      'landing.eyebrow': 'AI DOCUMENT ASSISTANT',
      'landing.headline': 'Business knowledge,',
      'landing.headlineAccent': 'ready when you need it',
      'landing.description':
        'Manage, find, and ask questions across your documents in one clear workspace with traceable sources and protected sessions.',
      'landing.primaryAction': 'Start now',
      'landing.secondaryAction': 'View workspace',
      'landing.productFrameLabel': 'Live workspace data from backend',
      'landing.product.workspaceName': 'Main workspace',
      'landing.product.overview': 'Overview',
      'landing.product.documents': 'Documents',
      'landing.product.insights': 'AI assistant',
      'landing.product.liveData': 'LIVE DATA',
      'landing.product.overviewTitle': 'Workspace overview',
      'landing.product.workspaceCount': 'Workspaces: {{count}}',
      'landing.product.documentCount': 'Documents: {{count}}',
      'landing.product.empty': 'No backend workspace yet.',
      'landing.sectionLabel': 'AI DOCUMENT PLATFORM',
      'landing.featuresTitle': 'Work through documents faster',
      'landing.featuresDescription':
        'Use AI to find information, verify sources, and protect workflows without exposing implementation details.',
      'landing.features.semantic-search.title': 'Search in plain language',
      'landing.features.semantic-search.description':
        'Ask naturally and get answers with clear source references.',
      'landing.features.hybrid-retrieval.title': 'Find the right source',
      'landing.features.hybrid-retrieval.description':
        'Bring together keywords, document content, and relevance signals so the right sources surface first.',
      'landing.features.analysis.title': 'Source-backed answers',
      'landing.features.analysis.description':
        'Every answer includes citations so you can verify the original document.',
      'landing.features.security.title': 'Protected sessions',
      'landing.features.security.description':
        'Each action is protected by a signed session, replay checks, and integrity validation.',
      'landing.securityPoints.session': 'Protected sign-in session',
      'landing.securityPoints.replay': 'Replay protection',
      'landing.statsLabel': 'Live metrics from backend',
      'landing.stats.workspaces': 'Backend workspaces',
      'landing.stats.documents': 'Available documents',
      'landing.stats.precision': 'Precise matching',
      'landing.stats.suggestionQuality': 'Relevant suggestions',
      'landing.cta.title': 'Ready to work with your knowledge base?',
      'landing.cta.description':
        'Select a backend workspace and start managing documents.',
      'landing.footer.security': 'Protected session',
      'state.loadingTitle': 'Connecting to backend',
      'state.loadingBody': 'Loading configuration and workspace through the signed API.',
      'state.errorTitle': 'Could not load data from backend',
      'state.retry': 'Retry',
      'status.on': 'ON',
      'status.off': 'OFF',
    },
  },
  ja: {
    translation: {
      'nav.features': '機能',
      'nav.platform': 'プラットフォーム',
      'nav.contact': 'お問い合わせ',
      'nav.landingNavigation': 'ランディングページのナビゲーション',
      'nav.workspaceNavigation': 'ワークスペースのナビゲーション',
      'nav.dashboard': 'ダッシュボード',
      'nav.workspaces': 'ワークスペース',
      'nav.intelligence': 'ドキュメントAI',
      'nav.language': '言語',
      'nav.home': 'ホームへ戻る',
      'nav.enterWorkspace': 'ワークスペースへ',
      'nav.signIn': 'ログイン',
      'nav.signOut': 'ログアウト',
      'landing.eyebrow': 'AIドキュメントアシスタント',
      'landing.headline': '企業のナレッジを、',
      'landing.headlineAccent': '必要なときにすぐ使える形へ',
      'landing.description':
        'ドキュメントの管理、検索、質問応答をひとつの明快なワークスペースで行い、参照元と安全なセッションも保ちます。',
      'landing.primaryAction': '今すぐ始める',
      'landing.secondaryAction': 'ワークスペースを見る',
      'landing.productFrameLabel': 'バックエンドからのライブワークスペースデータ',
      'landing.product.workspaceName': 'メインワークスペース',
      'landing.product.overview': '概要',
      'landing.product.documents': 'ドキュメント',
      'landing.product.insights': 'AIアシスタント',
      'landing.product.liveData': 'ライブデータ',
      'landing.product.overviewTitle': 'ワークスペース概要',
      'landing.product.workspaceCount': '{{count}} ワークスペース',
      'landing.product.documentCount': '{{count}} 件のドキュメント',
      'landing.product.empty': 'バックエンドにワークスペースがまだありません。',
      'landing.sectionLabel': 'AIドキュメントプラットフォーム',
      'landing.featuresTitle': 'ドキュメント作業をもっと速く',
      'landing.featuresDescription':
        '実装の仕組みを意識させずに、AIで情報検索、出典確認、安全なワークフローを支援します。',
      'landing.features.semantic-search.title': '自然な言葉で探す',
      'landing.features.semantic-search.description':
        '普段の言葉で質問すると、参照元がわかる回答を受け取れます。',
      'landing.features.hybrid-retrieval.title': '正しい資料にたどり着く',
      'landing.features.hybrid-retrieval.description':
        'キーワード、内容、関連度を組み合わせて、必要な資料を優先して表示します。',
      'landing.features.analysis.title': '出典つきの分析',
      'landing.features.analysis.description':
        '回答には参照情報が付くため、必要に応じて元の資料を確認できます。',
      'landing.features.security.title': '安全な作業セッション',
      'landing.features.security.description':
        '各操作はログインセッション、再送防止、整合性チェックで保護されます。',
      'landing.securityPoints.session': '保護されたログインセッション',
      'landing.securityPoints.replay': '再送リクエストを防止',
      'landing.statsLabel': 'バックエンドからのライブ指標',
      'landing.stats.workspaces': 'BEワークスペース',
      'landing.stats.documents': '利用可能な資料',
      'landing.stats.precision': '内容の一致',
      'landing.stats.suggestionQuality': '関連度の高い提案',
      'landing.cta.title': 'ナレッジベースを使う準備はできていますか？',
      'landing.cta.description':
        'バックエンドで検出されたワークスペースを選び、ドキュメント管理を始めましょう。',
      'landing.footer.security': '保護されたセッション',
      'state.loadingTitle': 'バックエンドへ接続中',
      'state.loadingBody': '署名付きAPIから設定とワークスペースを読み込んでいます。',
      'state.errorTitle': 'バックエンドからデータを読み込めませんでした',
      'state.retry': '再試行',
      'status.on': 'オン',
      'status.off': 'オフ',
    },
  },
} as const

i18n.use(initReactI18next).init({
  resources,
  lng: 'vi',
  fallbackLng: 'vi',
  supportedLngs: [...supportedLanguages],
  interpolation: {
    escapeValue: false,
  },
})


for (const translations of [pageTranslations, adminTranslations]) {
  for (const [language, translation] of Object.entries(translations)) {
    i18n.addResourceBundle(language, 'translation', translation, true, true)
  }
}
export default i18n
