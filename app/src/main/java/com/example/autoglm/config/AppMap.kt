package com.example.autoglm.config

/**
 * App name to package name mapping for supported applications.
 * 
 * 应用分类说明（所有应用均为Android平台应用）：
 * 
 * 1. 中文Android应用（60+个）
 *    - 社交通讯：微信、QQ、微博
 *    - 电商购物：淘宝、京东、拼多多、Temu
 *    - 生活服务：小红书、豆瓣、知乎、美团、大众点评、饿了么
 *    - 地图导航：高德地图、百度地图
 *    - 出行旅游：携程、12306、去哪儿、滴滴出行
 *    - 视频娱乐：bilibili、抖音、快手、腾讯视频、爱奇艺、优酷、芒果TV
 *    - 音乐音频：网易云音乐、QQ音乐、汽水音乐、喜马拉雅
 *    - 阅读：番茄小说、七猫免费小说
 *    - 办公生产力：飞书、QQ邮箱
 *    - AI工具：豆包
 *    - 健康健身：keep、美柚
 *    - 新闻资讯：腾讯新闻、今日头条
 *    - 房产：贝壳找房、安居客
 *    - 金融：同花顺
 *    - 游戏：星穹铁道、恋与深空
 * 
 * 2. Android系统应用（6个）
 *    - Settings、Chrome、Clock、Contacts、Files、AudioRecorder
 *    - 这些是Android原生系统应用，任何Android设备都有
 * 
 * 3. Google生态应用（30+个）
 *    - Gmail、Maps、Drive、Calendar、Docs、Slides、Keep、Tasks等
 *    - 这些是Google官方为Android开发的应用
 *    - 支持国际版Android设备和英文场景（AutoGLM-Phone-9B-Multilingual模型）
 * 
 * 4. 国际通用应用（20+个）
 *    - 社交媒体：WhatsApp、Telegram、Twitter/X、Reddit、TikTok、Quora
 *    - 生活服务：McDonald、Booking.com、Expedia
 *    - 工具应用：Duolingo、Joplin、VLC、Osmand等
 * 
 * 重要说明：
 * - 所有应用均为Android平台应用，无鸿蒙（HarmonyOS）专属应用
 * - 部分应用同时支持Android和鸿蒙，但包名在两个平台上相同（如微信）
 * - 完整同步自Python原版 phone_agent/config/apps.py（200+应用映射）
 * - 支持中文和英文场景，符合Open-AutoGLM的多语言设计目标
 * 
 * Phase 5: Memory & Assets - 已完成同步
 */
object AppMap {
    val PACKAGES = mapOf(
        // Social & Messaging (中文应用)
        "微信" to "com.tencent.mm",
        "QQ" to "com.tencent.mobileqq",
        "微博" to "com.sina.weibo",
        
        // E-commerce (中文应用)
        "淘宝" to "com.taobao.taobao",
        "京东" to "com.jingdong.app.mall",
        "拼多多" to "com.xunmeng.pinduoduo",
        "淘宝闪购" to "com.taobao.taobao",
        "京东秒送" to "com.jingdong.app.mall",
        
        // Lifestyle & Social (中文应用)
        "小红书" to "com.xingin.xhs",
        "豆瓣" to "com.douban.frodo",
        "知乎" to "com.zhihu.android",
        
        // Maps & Navigation (中文应用)
        "高德地图" to "com.autonavi.minimap",
        "百度地图" to "com.baidu.BaiduMap",
        
        // Food & Services (中文应用)
        "美团" to "com.sankuai.meituan",
        "大众点评" to "com.dianping.v1",
        "饿了么" to "me.ele",
        "肯德基" to "com.yek.android.kfc.activitys",
        
        // Travel (中文应用)
        "携程" to "ctrip.android.view",
        "铁路12306" to "com.MobileTicket",
        "12306" to "com.MobileTicket",
        "去哪儿" to "com.Qunar",
        "去哪儿旅行" to "com.Qunar",
        "滴滴出行" to "com.sdu.didi.psnger",
        
        // Video & Entertainment (中文应用)
        "bilibili" to "tv.danmaku.bili",
        "抖音" to "com.ss.android.ugc.aweme",
        "快手" to "com.smile.gifmaker",
        "腾讯视频" to "com.tencent.qqlive",
        "爱奇艺" to "com.qiyi.video",
        "优酷视频" to "com.youku.phone",
        "芒果TV" to "com.hunantv.imgo.activity",
        "红果短剧" to "com.phoenix.read",
        
        // Music & Audio (中文应用)
        "网易云音乐" to "com.netease.cloudmusic",
        "QQ音乐" to "com.tencent.qqmusic",
        "汽水音乐" to "com.luna.music",
        "喜马拉雅" to "com.ximalaya.ting.android",
        
        // Reading (中文应用)
        "番茄小说" to "com.dragon.read",
        "番茄免费小说" to "com.dragon.read",
        "七猫免费小说" to "com.kmxs.reader",
        
        // Productivity (中文应用)
        "飞书" to "com.ss.android.lark",
        "QQ邮箱" to "com.tencent.androidqqmail",
        
        // AI & Tools (中文应用)
        "豆包" to "com.larus.nova",
        
        // Health & Fitness (中文应用)
        "keep" to "com.gotokeep.keep",
        "美柚" to "com.lingan.seeyou",
        
        // News & Information (中文应用)
        "腾讯新闻" to "com.tencent.news",
        "今日头条" to "com.ss.android.article.news",
        
        // Real Estate (中文应用)
        "贝壳找房" to "com.lianjia.beike",
        "安居客" to "com.anjuke.android.app",
        
        // Finance (中文应用)
        "同花顺" to "com.hexin.plat.android",
        
        // Games (中文应用)
        "星穹铁道" to "com.miHoYo.hkrpg",
        "崩坏：星穹铁道" to "com.miHoYo.hkrpg",
        "恋与深空" to "com.papegames.lysk.cn",
        
        // Android System (英文应用 - 多种变体)
        "AndroidSystemSettings" to "com.android.settings",
        "Android System Settings" to "com.android.settings",
        "Android  System Settings" to "com.android.settings",
        "Android-System-Settings" to "com.android.settings",
        "Settings" to "com.android.settings",
        
        // Audio Recorder (英文应用 - 多种变体)
        "AudioRecorder" to "com.android.soundrecorder",
        "audiorecorder" to "com.android.soundrecorder",
        
        // Bluecoins (英文应用 - 多种变体)
        "Bluecoins" to "com.rammigsoftware.bluecoins",
        "bluecoins" to "com.rammigsoftware.bluecoins",
        
        // Broccoli (英文应用 - 多种变体)
        "Broccoli" to "com.flauschcode.broccoli",
        "broccoli" to "com.flauschcode.broccoli",
        
        // Booking.com (英文应用 - 多种变体)
        "Booking.com" to "com.booking",
        "Booking" to "com.booking",
        "booking.com" to "com.booking",
        "booking" to "com.booking",
        "BOOKING.COM" to "com.booking",
        
        // Chrome (英文应用 - 多种变体)
        "Chrome" to "com.android.chrome",
        "chrome" to "com.android.chrome",
        "Google Chrome" to "com.android.chrome",
        
        // Clock (英文应用 - 多种变体)
        "Clock" to "com.android.deskclock",
        "clock" to "com.android.deskclock",
        
        // Contacts (英文应用 - 多种变体)
        "Contacts" to "com.android.contacts",
        "contacts" to "com.android.contacts",
        
        // Duolingo (英文应用 - 多种变体)
        "Duolingo" to "com.duolingo",
        "duolingo" to "com.duolingo",
        
        // Expedia (英文应用 - 多种变体)
        "Expedia" to "com.expedia.bookings",
        "expedia" to "com.expedia.bookings",
        
        // Files (英文应用 - 多种变体)
        "Files" to "com.android.fileexplorer",
        "files" to "com.android.fileexplorer",
        "File Manager" to "com.android.fileexplorer",
        "file manager" to "com.android.fileexplorer",
        
        // Gmail (英文应用 - 多种变体)
        "gmail" to "com.google.android.gm",
        "Gmail" to "com.google.android.gm",
        "GoogleMail" to "com.google.android.gm",
        "Google Mail" to "com.google.android.gm",
        
        // Google Files (英文应用 - 多种变体)
        "GoogleFiles" to "com.google.android.apps.nbu.files",
        "googlefiles" to "com.google.android.apps.nbu.files",
        "FilesbyGoogle" to "com.google.android.apps.nbu.files",
        
        // Google Calendar (英文应用 - 多种变体)
        "GoogleCalendar" to "com.google.android.calendar",
        "Google-Calendar" to "com.google.android.calendar",
        "Google Calendar" to "com.google.android.calendar",
        "google-calendar" to "com.google.android.calendar",
        "google calendar" to "com.google.android.calendar",
        
        // Google Chat (英文应用 - 多种变体)
        "GoogleChat" to "com.google.android.apps.dynamite",
        "Google Chat" to "com.google.android.apps.dynamite",
        "Google-Chat" to "com.google.android.apps.dynamite",
        
        // Google Clock (英文应用 - 多种变体)
        "GoogleClock" to "com.google.android.deskclock",
        "Google Clock" to "com.google.android.deskclock",
        "Google-Clock" to "com.google.android.deskclock",
        
        // Google Contacts (英文应用 - 多种变体)
        "GoogleContacts" to "com.google.android.contacts",
        "Google-Contacts" to "com.google.android.contacts",
        "Google Contacts" to "com.google.android.contacts",
        "google-contacts" to "com.google.android.contacts",
        "google contacts" to "com.google.android.contacts",
        
        // Google Docs (英文应用 - 多种变体)
        "GoogleDocs" to "com.google.android.apps.docs.editors.docs",
        "Google Docs" to "com.google.android.apps.docs.editors.docs",
        "googledocs" to "com.google.android.apps.docs.editors.docs",
        "google docs" to "com.google.android.apps.docs.editors.docs",
        
        // Google Drive (英文应用 - 多种变体)
        "Google Drive" to "com.google.android.apps.docs",
        "Google-Drive" to "com.google.android.apps.docs",
        "google drive" to "com.google.android.apps.docs",
        "google-drive" to "com.google.android.apps.docs",
        "GoogleDrive" to "com.google.android.apps.docs",
        "Googledrive" to "com.google.android.apps.docs",
        "googledrive" to "com.google.android.apps.docs",
        
        // Google Fit (英文应用 - 多种变体)
        "GoogleFit" to "com.google.android.apps.fitness",
        "googlefit" to "com.google.android.apps.fitness",
        
        // Google Keep (英文应用 - 多种变体)
        "GoogleKeep" to "com.google.android.keep",
        "googlekeep" to "com.google.android.keep",
        
        // Google Maps (英文应用 - 多种变体)
        "GoogleMaps" to "com.google.android.apps.maps",
        "Google Maps" to "com.google.android.apps.maps",
        "googlemaps" to "com.google.android.apps.maps",
        "google maps" to "com.google.android.apps.maps",
        
        // Google Play Books (英文应用 - 多种变体)
        "Google Play Books" to "com.google.android.apps.books",
        "Google-Play-Books" to "com.google.android.apps.books",
        "google play books" to "com.google.android.apps.books",
        "google-play-books" to "com.google.android.apps.books",
        "GooglePlayBooks" to "com.google.android.apps.books",
        "googleplaybooks" to "com.google.android.apps.books",
        
        // Google Play Store (英文应用 - 多种变体)
        "GooglePlayStore" to "com.android.vending",
        "Google Play Store" to "com.android.vending",
        "Google-Play-Store" to "com.android.vending",
        
        // Google Slides (英文应用 - 多种变体)
        "GoogleSlides" to "com.google.android.apps.docs.editors.slides",
        "Google Slides" to "com.google.android.apps.docs.editors.slides",
        "Google-Slides" to "com.google.android.apps.docs.editors.slides",
        
        // Google Tasks (英文应用 - 多种变体)
        "GoogleTasks" to "com.google.android.apps.tasks",
        "Google Tasks" to "com.google.android.apps.tasks",
        "Google-Tasks" to "com.google.android.apps.tasks",
        
        // Joplin (英文应用 - 多种变体)
        "Joplin" to "net.cozic.joplin",
        "joplin" to "net.cozic.joplin",
        
        // McDonald (英文应用 - 多种变体)
        "McDonald" to "com.mcdonalds.app",
        "mcdonald" to "com.mcdonalds.app",
        
        // Osmand (英文应用 - 多种变体)
        "Osmand" to "net.osmand",
        "osmand" to "net.osmand",
        
        // Pi Music Player (英文应用 - 多种变体)
        "PiMusicPlayer" to "com.Project100Pi.themusicplayer",
        "pimusicplayer" to "com.Project100Pi.themusicplayer",
        
        // Quora (英文应用 - 多种变体)
        "Quora" to "com.quora.android",
        "quora" to "com.quora.android",
        
        // Reddit (英文应用 - 多种变体)
        "Reddit" to "com.reddit.frontpage",
        "reddit" to "com.reddit.frontpage",
        
        // Retro Music (英文应用 - 多种变体)
        "RetroMusic" to "code.name.monkey.retromusic",
        "retromusic" to "code.name.monkey.retromusic",
        
        // Simple Calendar Pro (英文应用)
        "SimpleCalendarPro" to "com.scientificcalculatorplus.simplecalculator.basiccalculator.mathcalc",
        
        // Simple SMS Messenger (英文应用)
        "SimpleSMSMessenger" to "com.simplemobiletools.smsmessenger",
        
        // Telegram (英文应用)
        "Telegram" to "org.telegram.messenger",
        
        // Temu (英文应用 - 多种变体)
        "temu" to "com.einnovation.temu",
        "Temu" to "com.einnovation.temu",
        
        // TikTok (英文应用 - 多种变体)
        "Tiktok" to "com.zhiliaoapp.musically",
        "tiktok" to "com.zhiliaoapp.musically",
        
        // Twitter / X (英文应用 - 多种变体)
        "Twitter" to "com.twitter.android",
        "twitter" to "com.twitter.android",
        "X" to "com.twitter.android",
        
        // VLC (英文应用)
        "VLC" to "org.videolan.vlc",
        
        // WeChat (英文应用 - 多种变体)
        "WeChat" to "com.tencent.mm",
        "wechat" to "com.tencent.mm",
        
        // WhatsApp (英文应用 - 多种变体)
        "Whatsapp" to "com.whatsapp",
        "WhatsApp" to "com.whatsapp"
    )
    
    /**
     * 根据应用名获取包名
     * 严格对齐原版 Python apps.py 的 get_package_name()
     * 
     * @param appName 应用名（支持大小写不敏感匹配）
     * @return 包名，未找到返回 null
     */
    fun getPackageName(appName: String): String? {
        // 精确匹配
        PACKAGES[appName]?.let { return it }
        
        // 大小写不敏感匹配
        PACKAGES.entries.find { it.key.equals(appName, ignoreCase = true) }?.let { 
            return it.value 
        }
        
        return null
    }
    
    /**
     * 根据包名反向查询应用名
     * 严格对齐原版 Python apps.py 的 get_app_name()
     * 
     * @param packageName 包名
     * @return 应用名，未找到返回 null
     */
    fun getAppName(packageName: String): String? {
        return PACKAGES.entries.find { it.value == packageName }?.key
    }
    
    /**
     * 获取所有支持的应用名列表
     * 严格对齐原版 Python apps.py 的 list_supported_apps()
     * 
     * @return 应用名列表
     */
    fun listSupportedApps(): List<String> {
        return PACKAGES.keys.toList()
    }
}
