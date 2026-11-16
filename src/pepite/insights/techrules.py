"""Technology fingerprint rules, written for Pépite (not copied from a third-party dataset).

Each rule names a technology, its category and the public signals that reveal it. Signals are
regular expressions (case-insensitive) over one of:
  html    raw page source (inline scripts, markup, comments)
  src     URLs in script/link/img/iframe tags
  meta    "name: content" of <meta> tags
  header  HTTP response headers of the homepage: {"header-name": regex or "" for presence}
  cookie  cookie names set by the homepage
  dns     ("mx" | "txt", regex) over the domain's MX hosts or TXT records (SPF, verification)
Only signals that a normal visitor's browser or mail server would also see are used.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CMS = "CMS"
SHOP = "E-commerce"
ANALYTICS = "Analytics"
ADS = "Advertising"
TAGS = "Tag manager"
MARKETING = "Marketing automation"
FORMS = "CRM and forms"
CHAT = "Chat and support"
PAY = "Payments"
JS = "JavaScript framework"
CDN = "CDN and hosting"
SERVER = "Web server"
ABTEST = "A/B testing"
CONSENT = "Consent management"
EMAIL = "Email provider"
TOOLS = "Business tools"


@dataclass(frozen=True)
class Rule:
    name: str
    category: str
    html: re.Pattern[str] | None = None
    src: re.Pattern[str] | None = None
    meta: re.Pattern[str] | None = None
    header: tuple[tuple[str, re.Pattern[str] | None], ...] = ()
    cookie: re.Pattern[str] | None = None
    dns: tuple[tuple[str, re.Pattern[str]], ...] = ()


def _rx(p: str | None) -> re.Pattern[str] | None:
    return re.compile(p, re.IGNORECASE) if p else None


RULES: list[Rule] = []


def T(  # noqa: N802 - declarative table helper
    name: str,
    category: str,
    *,
    html: str | None = None,
    src: str | None = None,
    meta: str | None = None,
    header: dict[str, str] | None = None,
    cookie: str | None = None,
    dns: list[tuple[str, str]] | None = None,
) -> None:
    RULES.append(
        Rule(
            name,
            category,
            _rx(html),
            _rx(src),
            _rx(meta),
            tuple((k.lower(), _rx(v)) for k, v in (header or {}).items()),
            _rx(cookie),
            tuple((k, re.compile(v, re.IGNORECASE)) for k, v in (dns or [])),
        )
    )


# fmt: off
# -- CMS and site builders ----------------------------------------------------------------
T("WordPress", CMS, src=r"/wp-(?:content|includes)/", meta=r"^generator: WordPress", html=r"/wp-json/", header={"link": r"wp-json"})
T("Drupal", CMS, meta=r"^generator: Drupal", html=r"drupal-settings-json|Drupal\.settings", header={"x-generator": r"Drupal", "x-drupal-cache": ""})
T("Joomla", CMS, meta=r"^generator: Joomla", html=r"/media/jui/|/components/com_")
T("TYPO3", CMS, meta=r"^generator: TYPO3", html=r"typo3conf/|typo3temp/")
T("Wix", CMS, src=r"static\.parastorage\.com|wixstatic\.com", meta=r"^generator: Wix", header={"x-wix-request-id": ""})
T("Squarespace", CMS, src=r"static1\.squarespace\.com|squarespace-cdn\.com", html=r"<!-- This is Squarespace", meta=r"^generator: Squarespace")
T("Webflow", CMS, html=r"data-wf-(?:site|page)=", src=r"assets(?:-global)?\.website-files\.com|uploads-ssl\.webflow\.com", meta=r"^generator: Webflow")
T("Ghost", CMS, meta=r"^generator: Ghost", header={"x-ghost-cache-status": ""})
T("Weebly", CMS, src=r"editmysite\.com|weebly\.com/files", html=r"_W\.configDomain")
T("Framer", CMS, meta=r"^generator: Framer", src=r"framerusercontent\.com|framer\.com/m/")
T("Duda", CMS, src=r"multiscreensite\.com|irp\.cdn-website\.com|dudaone\.com")
T("Craft CMS", CMS, header={"x-powered-by": r"Craft CMS"}, cookie=r"^CraftSessionId$")
T("Sitecore", CMS, cookie=r"^SC_ANALYTICS_GLOBAL_COOKIE$", html=r"/-/media/")
T("Adobe Experience Manager", CMS, html=r"/etc\.clientlibs/|/etc/designs/", src=r"/etc\.clientlibs/")
T("Umbraco", CMS, meta=r"^generator: umbraco", html=r"/umbraco/")
T("Strapi", CMS, header={"x-powered-by": r"Strapi"})
T("Hugo", CMS, meta=r"^generator: Hugo")
T("Jekyll", CMS, meta=r"^generator: Jekyll")
T("Gatsby", CMS, meta=r"^generator: Gatsby", html=r"id=\"___gatsby\"")
T("HubSpot CMS", CMS, meta=r"^generator: HubSpot", src=r"hs-sites\.com|hubspotusercontent\d*\.net|hs-scripts\.com/\d+\.js", header={"x-hs-hub-id": ""})
T("Odoo", CMS, meta=r"^generator: Odoo", html=r"/web/assets/|odoo\.com")
T("Elementor", CMS, html=r"elementor-(?:element|widget|section)|/plugins/elementor/", src=r"/plugins/elementor/")
T("Divi", CMS, html=r"et_pb_section|/themes/Divi/", src=r"/themes/Divi/")
T("Contentful", CMS, src=r"images\.ctfassets\.net|assets\.ctfassets\.net")
T("Sanity", CMS, src=r"cdn\.sanity\.io")
T("Prismic", CMS, src=r"images\.prismic\.io|static\.cdn\.prismic\.io")
T("Shopify", SHOP, src=r"cdn\.shopify\.com|shopifycloud\.com", header={"x-shopid": "", "x-shopify-stage": ""}, html=r"Shopify\.theme|window\.Shopify", cookie=r"^_shopify_[sy]$|^cart_sig$")

# -- E-commerce ---------------------------------------------------------------------------
T("WooCommerce", SHOP, html=r"woocommerce-(?:page|js|no-js)|/plugins/woocommerce/", src=r"/plugins/woocommerce/", cookie=r"^woocommerce_(?:cart_hash|items_in_cart)$")
T("Magento", SHOP, html=r"Mage\.Cookies|/static/frontend/Magento|mage/cookies", src=r"/static/(?:version\d+/)?frontend/", cookie=r"^(?:mage-cache-sessid|PHPSESSID_MAGE|form_key)$")
T("PrestaShop", SHOP, meta=r"^generator: PrestaShop", html=r"prestashop\s*=|/modules/ps_", cookie=r"^PrestaShop-")
T("BigCommerce", SHOP, src=r"cdn\d*\.bigcommerce\.com|bigcommerce\.com/s-", html=r"bigcommerce")
T("Shopware", SHOP, meta=r"^(?:generator|application-name): Shopware", html=r"/bundles/storefront/|sw-cms-", cookie=r"^sw-(?:cache-hash|states)$")
T("OpenCart", SHOP, html=r"catalog/view/theme/|index\.php\?route=", src=r"catalog/view/(?:theme|javascript)/")
T("Salesforce Commerce Cloud", SHOP, src=r"/on/demandware\.static/", html=r"demandware\.(?:static|store)", cookie=r"^dwsid$")
T("Ecwid", SHOP, src=r"app\.ecwid\.com|ecwid\.com/script\.js")
T("Snipcart", SHOP, src=r"cdn\.snipcart\.com", html=r"snipcart")
T("Lightspeed eCom", SHOP, src=r"assets\.webshopapp\.com|cdn\.shoplightspeed\.com")
T("Wix Stores", SHOP, html=r"wixstores|wix-ecommerce")

# -- Analytics ----------------------------------------------------------------------------
T("Google Analytics", ANALYTICS, src=r"google-analytics\.com/(?:analytics|ga)\.js|googletagmanager\.com/gtag/js\?id=(?:G|UA)-", html=r"gtag\(\s*['\"]config['\"]\s*,\s*['\"](?:G|UA)-", cookie=r"^_ga(?:_[A-Z0-9]+)?$|^_gid$")
T("Google Ads", ADS, src=r"googleadservices\.com|googleads\.g\.doubleclick\.net", html=r"gtag\(\s*['\"]config['\"]\s*,\s*['\"]AW-")
T("Matomo", ANALYTICS, src=r"matomo\.js|piwik\.js|/matomo/|/piwik/", html=r"_paq\.push", cookie=r"^_pk_(?:id|ses)\.")
T("Plausible", ANALYTICS, src=r"plausible\.io/js/")
T("Fathom", ANALYTICS, src=r"cdn\.usefathom\.com")
T("Simple Analytics", ANALYTICS, src=r"scripts\.simpleanalyticscdn\.com|cdn\.simpleanalytics\.io")
T("Hotjar", ANALYTICS, src=r"static\.hotjar\.com|script\.hotjar\.com", html=r"hj\(\s*['\"]identify|_hjSettings", cookie=r"^_hj(?:SessionUser|Session|FirstSeen)")
T("Microsoft Clarity", ANALYTICS, src=r"clarity\.ms/tag/", html=r"clarity\(\s*['\"]set", cookie=r"^_clck$|^_clsk$")
T("Mixpanel", ANALYTICS, src=r"cdn\.mxpnl\.com|mixpanel\.com/libs", html=r"mixpanel\.init", cookie=r"^mp_.+_mixpanel$")
T("Amplitude", ANALYTICS, src=r"cdn\.amplitude\.com|amplitude\.com/libs", html=r"amplitude\.getInstance")
T("Segment", ANALYTICS, src=r"cdn\.segment\.(?:com|io)|segment\.com/analytics\.js", html=r"analytics\.load\(", cookie=r"^ajs_(?:anonymous_id|user_id)$")
T("Heap", ANALYTICS, src=r"cdn\.heapanalytics\.com", html=r"heap\.load\(")
T("FullStory", ANALYTICS, src=r"fullstory\.com/s/fs\.js|edge\.fullstory\.com", html=r"window\['_fs_host'\]")
T("Crazy Egg", ANALYTICS, src=r"script\.crazyegg\.com")
T("Mouseflow", ANALYTICS, src=r"cdn\.mouseflow\.com", cookie=r"^mf_user$")
T("Lucky Orange", ANALYTICS, src=r"luckyorange\.(?:com|net)")
T("PostHog", ANALYTICS, src=r"(?:eu|us|app)\.(?:i\.)?posthog\.com|posthog\.js|array\.js", html=r"posthog\.init", cookie=r"^ph_phc_")
T("Pendo", ANALYTICS, src=r"cdn\.pendo\.io|pendo-static")
T("Adobe Analytics", ANALYTICS, src=r"AppMeasurement\.js|omniture|/s_code\.js|\.omtrdc\.net", cookie=r"^s_(?:vi|fid|cc)$|^AMCV_")
T("Yandex Metrica", ANALYTICS, src=r"mc\.yandex\.(?:ru|com)/metrika", html=r"ym\(\d+,\s*['\"]init", cookie=r"^_ym_uid$")
T("Piwik PRO", ANALYTICS, src=r"piwik\.pro|containers\.piwik\.pro")
T("Cloudflare Web Analytics", ANALYTICS, src=r"static\.cloudflareinsights\.com/beacon")
T("Vercel Analytics", ANALYTICS, src=r"/_vercel/insights/|va\.vercel-scripts\.com")
T("StatCounter", ANALYTICS, src=r"statcounter\.com/counter")
T("Woopra", ANALYTICS, src=r"static\.woopra\.com")
T("Kissmetrics", ANALYTICS, src=r"i\.kissmetrics\.(?:com|io)|scripts\.kissmetrics")
T("Snowplow", ANALYTICS, src=r"sp\.js|snowplow", html=r"snowplow\(")
T("Meta Pixel", ADS, src=r"connect\.facebook\.net/[^/]+/fbevents\.js", html=r"fbq\(\s*['\"]init['\"]")
T("LinkedIn Insight Tag", ADS, src=r"snap\.licdn\.com/li\.lms-analytics", html=r"_linkedin_partner_id|_linkedin_data_partner_id", cookie=r"^li_fat_id$|^bcookie$")
T("TikTok Pixel", ADS, src=r"analytics\.tiktok\.com", html=r"ttq\.load\(")
T("X (Twitter) Pixel", ADS, src=r"static\.ads-twitter\.com/uwt\.js", html=r"twq\(\s*['\"]init")
T("Pinterest Tag", ADS, src=r"s\.pinimg\.com/ct/core\.js", html=r"pintrk\(\s*['\"]load")
T("Criteo", ADS, src=r"static\.criteo\.net|dis\.criteo\.com")
T("Microsoft Advertising (UET)", ADS, src=r"bat\.bing\.com/bat\.js", html=r"UET\(|uetq")
T("Google AdSense", ADS, src=r"pagead2\.googlesyndication\.com")
T("Snap Pixel", ADS, src=r"sc-static\.net/scevent\.min\.js")
T("Taboola", ADS, src=r"cdn\.taboola\.com")
T("Outbrain", ADS, src=r"outbrain\.com/outbrain\.js|widgets\.outbrain\.com")

# -- Tag managers -------------------------------------------------------------------------
T("Google Tag Manager", TAGS, src=r"googletagmanager\.com/gtm\.js", html=r"GTM-[A-Z0-9]{4,8}")
T("Tealium", TAGS, src=r"tags\.tiqcdn\.com|tealiumiq\.com", html=r"utag_data")
T("Adobe Launch", TAGS, src=r"assets\.adobedtm\.com")
T("Ensighten", TAGS, src=r"nexus\.ensighten\.com")
T("Matomo Tag Manager", TAGS, src=r"/js/container_[A-Za-z0-9]+\.js", html=r"_mtm\.push")

# -- Marketing automation, CRM and forms --------------------------------------------------
T("HubSpot", MARKETING, src=r"js\.hs-scripts\.com|js\.hsforms\.net|js\.hs-analytics\.net|js\.hubspot\.com|js\.usemessages\.com|forms\.hsforms\.com", html=r"_hsq\s*=|hbspt\.forms\.create", cookie=r"^hubspotutk$|^__hs[a-z]+$")
T("Marketo", MARKETING, src=r"munchkin\.marketo\.net|app-[a-z0-9]+\.marketo\.com|mktoForms2", html=r"Munchkin\.init|mktoForm", cookie=r"^_mkto_trk$")
T("Pardot", MARKETING, src=r"pi\.pardot\.com|cdn\.pardot\.com", html=r"piAId\s*=", cookie=r"^visitor_id\d+")
T("Salesforce Marketing Cloud", MARKETING, src=r"cdn\.evgnet\.com|exacttarget\.com|cloud\.email\.[a-z.]+/")
T("ActiveCampaign", MARKETING, src=r"trackcmp\.net|activehosted\.com|activecampaign\.com", html=r"trackcmp\.net")
T("Mailchimp", MARKETING, src=r"chimpstatic\.com|list-manage\.com|mailchimp\.com/mc", html=r"mc-embedded-subscribe-form|list-manage\.com/subscribe")
T("Klaviyo", MARKETING, src=r"static\.klaviyo\.com|klaviyo\.com/onsite", html=r"_learnq\s*=")
T("Brevo (Sendinblue)", MARKETING, src=r"sibautomation\.com|sendinblue\.com|brevo\.com|sibforms\.com", html=r"sib_embed|sendinblue")
T("Oracle Eloqua", MARKETING, src=r"img\.en25\.com|elqCfg|eloqua\.com", html=r"elqCurESite|elqSiteID")
T("Mautic", MARKETING, src=r"/mtc\.js|mautic", html=r"mt\('send',\s*'pageview'", cookie=r"^mtc_id$|^mautic_session_id$")
T("Drip", MARKETING, src=r"tag\.getdrip\.com", html=r"_dc\.push|_dcq")
T("ConvertKit (Kit)", MARKETING, src=r"f\.convertkit\.com|convertkit\.com/(?:forms|assets)|kit\.com/(?:forms|assets)", html=r"data-sv-form")
T("Constant Contact", MARKETING, src=r"ctctcdn\.com|constantcontact\.com", html=r"ctct")
T("GetResponse", MARKETING, src=r"getresponse\.com|gr-cdn\.com", html=r"GrWebFormComponent")
T("Customer.io", MARKETING, src=r"track\.customer\.io|assets\.customer\.io", html=r"_cio\.identify|_cio\s*=")
T("Iterable", MARKETING, src=r"iterable\.com|js\.iterable")
T("Omnisend", MARKETING, src=r"omnisnippet1\.com|omnisrc\.com|omnisend\.com", html=r"window\.omnisend")
T("Act-On", MARKETING, src=r"acton\.com|cdn\.actonsoftware\.com|actonservice\.com", html=r"ao_jsTagUrl")
T("Typeform", FORMS, src=r"embed\.typeform\.com|typeform\.com/to/", html=r"data-tf-(?:live|widget)")
T("Jotform", FORMS, src=r"jotfor\.ms|jotform\.com", html=r"jotform")
T("Gravity Forms", FORMS, html=r"gform_wrapper|gform_fields|/plugins/gravityforms/", src=r"/plugins/gravityforms/")
T("Contact Form 7", FORMS, html=r"wpcf7|/plugins/contact-form-7/", src=r"/plugins/contact-form-7/")
T("Formstack", FORMS, src=r"formstack\.com|formstack-form", html=r"formstack")
T("Wufoo", FORMS, src=r"wufoo\.com", html=r"wufoo")
T("Calendly", FORMS, src=r"assets\.calendly\.com|calendly\.com/assets", html=r"calendly\.com/[a-z0-9_-]+|calendly-inline-widget")
T("HubSpot Meetings", FORMS, src=r"static\.hsappstatic\.net/MeetingsEmbed", html=r"meetings\.hubspot\.com")
T("Pipedrive", FORMS, src=r"web-forms\.pipedrive\.com|leadbooster-chat\.pipedrive\.com|pipedriveassets\.com", html=r"pipedrive-form")
T("Zoho Forms and CRM", FORMS, src=r"forms\.zohopublic\.(?:com|eu)|crm\.zoho\.(?:com|eu)|zohopublic\.|zohoforms|zoho\.com/crm", html=r"zohoforms")
T("Salesforce Web-to-Lead", FORMS, html=r"servlet/servlet\.WebToLead|webto\.salesforce\.com", src=r"force\.com|salesforce\.com")
T("ZoomInfo WebSights", FORMS, src=r"ws\.zoominfo\.com|js\.zi-scripts\.com", html=r"ZoomInfo")
T("Leadfeeder (Dealfront)", FORMS, src=r"lftracker\.leadfeeder\.com|dealfront\.com", html=r"ldfdr")
T("Albacross", FORMS, src=r"serve\.albacross\.com", html=r"_nQc")
T("6sense", FORMS, src=r"j\.6sc\.co|6sc\.co", html=r"_6si")
T("Demandbase", FORMS, src=r"tag\.demandbase\.com|scripts\.demandbase\.com")
T("Clearbit", FORMS, src=r"tag\.clearbitscripts\.com|reveal\.clearbit\.com")

# -- Chat and support widgets -------------------------------------------------------------
T("Intercom", CHAT, src=r"widget\.intercom\.io|js\.intercomcdn\.com", html=r"window\.intercomSettings|Intercom\(\s*['\"]boot", cookie=r"^intercom-(?:id|session)-")
T("Drift", CHAT, src=r"js\.driftt\.com|drift\.com/include", html=r"drift\.load\(|driftt")
T("Zendesk", CHAT, src=r"static\.zdassets\.com|v2\.zopim\.com|zendesk\.com/embeddable", html=r"zE\(|zopim")
T("Crisp", CHAT, src=r"client\.crisp\.chat", html=r"CRISP_WEBSITE_ID")
T("Tawk.to", CHAT, src=r"embed\.tawk\.to", html=r"Tawk_API")
T("LiveChat", CHAT, src=r"cdn\.livechatinc\.com|livechatinc\.com/tracking", html=r"__lc\.license")
T("Freshchat", CHAT, src=r"wchat\.freshchat\.com|snippets\.freshchat\.com", html=r"fcWidget")
T("Freshdesk", CHAT, src=r"freshdesk\.com|freshworks\.com/widgets", html=r"FreshworksWidget")
T("Olark", CHAT, src=r"static\.olark\.com", html=r"olark\.identify|olark\.configure")
T("Tidio", CHAT, src=r"code\.tidio\.co|tidiochat", html=r"tidioChatApi")
T("Help Scout Beacon", CHAT, src=r"beacon-v2\.helpscout\.net|helpscout\.net", html=r"Beacon\(\s*['\"]init")
T("Userlike", CHAT, src=r"userlike-cdn-widgets\.s3-eu-west-1\.amazonaws\.com|userlike\.com")
T("Smartsupp", CHAT, src=r"smartsuppchat\.com", html=r"_smartsupp")
T("Gorgias", CHAT, src=r"config\.gorgias\.chat|gorgias\.chat|gorgias\.io")
T("Front Chat", CHAT, src=r"chat-assets\.frontapp\.com|frontapp\.com")
T("Zoho SalesIQ", CHAT, src=r"salesiq\.zoho\.(?:com|eu)|zohopublic\.com/salesiq", html=r"\$zoho\.salesiq")
T("Chatra", CHAT, src=r"call\.chatra\.io", html=r"ChatraID")
T("LiveAgent", CHAT, src=r"ladesk\.com|liveagent\.com", html=r"LiveAgent")
T("Kustomer", CHAT, src=r"cdn\.kustomerapp\.com|kustomer\.com")
T("HubSpot Conversations", CHAT, src=r"js\.usemessages\.com|js\.hs-scripts\.com/\d+\.js", html=r"HubSpotConversations")
T("Botpress", CHAT, src=r"cdn\.botpress\.cloud|botpress\.cloud")
T("Podium", CHAT, src=r"connect\.podium\.com|podium\.com/widget")
T("Trengo", CHAT, src=r"static\.widget\.trengo\.eu|trengo\.com", html=r"Trengo")

# -- Payments -----------------------------------------------------------------------------
T("Stripe", PAY, src=r"js\.stripe\.com|checkout\.stripe\.com|buy\.stripe\.com", html=r"Stripe\(\s*['\"]pk_", cookie=r"^__stripe_(?:mid|sid)$")
T("PayPal", PAY, src=r"paypal\.com/sdk/js|paypalobjects\.com", html=r"paypal\.Buttons|paypal\.me/")
T("Braintree", PAY, src=r"js\.braintreegateway\.com|assets\.braintreegateway\.com", html=r"braintree\.client\.create")
T("Adyen", PAY, src=r"checkoutshopper-(?:live|test)\.adyen\.com|adyen\.com/checkoutshopper", html=r"AdyenCheckout")
T("Klarna", PAY, src=r"x\.klarnacdn\.net|js\.klarna\.com|klarna\.com", html=r"klarna-placement")
T("Square", PAY, src=r"web\.squarecdn\.com|squareup\.com|square\.site", html=r"Square\.payments")
T("Mollie", PAY, src=r"js\.mollie\.com|mollie\.com")
T("Checkout.com", PAY, src=r"checkout-web-components|cdn\.checkout\.com|js\.checkout\.com")
T("Authorize.net", PAY, src=r"accept\.authorize\.net|js\.authorize\.net")
T("Razorpay", PAY, src=r"checkout\.razorpay\.com")
T("Paddle", PAY, src=r"cdn\.paddle\.com|paddle\.js", html=r"Paddle\.Setup")
T("GoCardless", PAY, src=r"pay\.gocardless\.com|gocardless\.com")
T("Afterpay", PAY, src=r"js\.afterpay\.com|afterpay\.com|portal\.afterpay\.com")
T("Affirm", PAY, src=r"cdn1\.affirm\.com|affirm\.com/js")
T("Lemon Squeezy", PAY, src=r"assets\.lemonsqueezy\.com|lemonsqueezy\.com")
T("Payplug", PAY, src=r"api\.payplug\.com|payplug\.com")
T("Worldpay", PAY, src=r"worldpay\.com|worldpay\.js")
T("Shop Pay", PAY, src=r"shop\.app|shopify-pay|shop_pay", html=r"shop-pay-button|shopify-payment-button")

# -- JavaScript frameworks and UI libraries ----------------------------------------------
T("React", JS, html=r"data-reactroot|__REACT_DEVTOOLS_GLOBAL_HOOK__|react-dom", src=r"react(?:-dom)?(?:\.min|\.production)?\.js|/react@")
T("Vue.js", JS, html=r"data-v-[0-9a-f]{6,8}|__VUE__|id=\"app\"[^>]*data-server-rendered", src=r"vue(?:\.runtime)?(?:\.min|\.global)?\.js|/vue@")
T("Angular", JS, html=r"ng-version=|<app-root", src=r"/(?:main|polyfills|runtime)\.[0-9a-f]{8,}\.js", header={"x-powered-by": r"Angular"})
T("AngularJS", JS, html=r"\bng-app=|\bng-controller=", src=r"angular(?:\.min)?\.js")
T("Svelte", JS, html=r"class=\"svelte-[a-z0-9]+|svelte-[a-z0-9]{6,}", src=r"/_app/immutable/")
T("Next.js", JS, html=r"id=\"__next\"|__NEXT_DATA__|/_next/static/", src=r"/_next/static/", header={"x-powered-by": r"Next\.js", "x-nextjs-cache": ""})
T("Nuxt", JS, html=r"id=\"__nuxt\"|window\.__NUXT__", src=r"/_nuxt/")
T("Astro", JS, meta=r"^generator: Astro", html=r"astro-island|data-astro-cid", src=r"/_astro/")
T("Remix", JS, html=r"__remixContext")
T("jQuery", JS, src=r"jquery(?:[-.]\d[\d.]*)?(?:\.min)?\.js|code\.jquery\.com", html=r"jQuery\s*(?:v|JavaScript Library)")
T("Bootstrap", JS, src=r"bootstrap(?:\.bundle)?(?:\.min)?\.(?:js|css)|/bootstrap@", html=r"class=\"[^\"]*\bcol-(?:xs|sm|md|lg|xl)-\d")
T("Tailwind CSS", JS, src=r"cdn\.tailwindcss\.com|tailwind(?:\.min)?\.css", html=r"class=\"[^\"]*\b(?:sm|md|lg):(?:flex|grid|px-\d|text-)")
T("Alpine.js", JS, html=r"\bx-data=|\bx-init=", src=r"alpinejs|/alpine(?:\.min)?\.js")
T("HTMX", JS, html=r"\bhx-(?:get|post|trigger)=", src=r"htmx(?:\.min)?\.js")
T("Ember.js", JS, html=r"ember-application|data-ember-action", src=r"ember(?:\.min)?\.js")
T("Preact", JS, src=r"preact(?:\.min)?\.js|/preact@")
T("Lodash", JS, src=r"lodash(?:\.min)?\.js")
T("Font Awesome", JS, src=r"fontawesome|font-awesome|use\.fontawesome\.com", html=r"class=\"[^\"]*\bfa[srb]? fa-")
T("Google Maps", JS, src=r"maps\.googleapis\.com/maps/api/js|maps\.google\.com/maps")
T("Mapbox", JS, src=r"api\.mapbox\.com|mapbox-gl")
T("YouTube embed", JS, src=r"youtube(?:-nocookie)?\.com/embed|youtube\.com/iframe_api")
T("Vimeo embed", JS, src=r"player\.vimeo\.com|f\.vimeocdn\.com")
T("Wistia", JS, src=r"fast\.wistia\.(?:com|net)|wistia\.com/embed")
T("reCAPTCHA", JS, src=r"google\.com/recaptcha|gstatic\.com/recaptcha")
T("hCaptcha", JS, src=r"hcaptcha\.com/1/api\.js")
T("Cloudflare Turnstile", JS, src=r"challenges\.cloudflare\.com/turnstile")

# -- CDN, hosting and web servers ---------------------------------------------------------
T("Cloudflare", CDN, header={"server": r"^cloudflare$", "cf-ray": ""}, cookie=r"^__cf_bm$|^__cfduid$|^cf_clearance$", src=r"/cdn-cgi/")
T("Fastly", CDN, header={"x-served-by": r"cache-", "fastly-debug-digest": "", "x-fastly-request-id": ""}, src=r"fastly\.net")
T("Akamai", CDN, header={"x-akamai-transformed": "", "akamai-grn": "", "server": r"AkamaiGHost|AkamaiNetStorage"}, src=r"akamaihd\.net|akamaized\.net|edgekey\.net")
T("Amazon CloudFront", CDN, header={"x-amz-cf-id": "", "via": r"CloudFront"}, src=r"cloudfront\.net")
T("Amazon S3", CDN, header={"x-amz-request-id": "", "server": r"^AmazonS3$"}, src=r"\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com")
T("Vercel", CDN, header={"x-vercel-id": "", "server": r"^Vercel$"})
T("Netlify", CDN, header={"x-nf-request-id": "", "server": r"^Netlify$"})
T("GitHub Pages", CDN, header={"server": r"^GitHub\.com$", "x-github-request-id": ""})
T("Firebase Hosting", CDN, header={"x-firebase-hosting": ""}, src=r"firebaseapp\.com|firebasestorage\.googleapis\.com|/__/firebase/")
T("Google Cloud", CDN, header={"via": r"google", "server": r"^(?:Google Frontend|gws|ESF)$", "x-cloud-trace-context": ""}, src=r"storage\.googleapis\.com")
T("Microsoft Azure", CDN, header={"x-azure-ref": "", "x-ms-request-id": "", "x-msedge-ref": ""}, src=r"azureedge\.net|blob\.core\.windows\.net|azurewebsites\.net")
T("Heroku", CDN, header={"via": r"heroku", "server": r"^Cowboy$"})
T("WP Engine", CDN, header={"x-powered-by": r"WP Engine", "x-wpe-loopback-upstream-addr": ""}, src=r"wpenginepowered\.com|wpengine\.com")
T("Kinsta", CDN, header={"x-kinsta-cache": ""}, src=r"kinsta\.cloud")
T("Pantheon", CDN, header={"x-pantheon-styx-hostname": "", "x-styx-req-id": ""})
T("Render", CDN, header={"x-render-origin-server": ""})
T("Fly.io", CDN, header={"fly-request-id": "", "server": r"^Fly\.io$"})
T("OVHcloud", CDN, header={"x-ovh-cdn": ""}, src=r"ovh\.net|ovhcloud\.com")
T("Imperva", CDN, header={"x-iinfo": "", "x-cdn": r"Imperva|Incapsula"}, cookie=r"^incap_ses_|^visid_incap_|^nlbi_")
T("Sucuri", CDN, header={"x-sucuri-id": "", "server": r"Sucuri"})
T("Bunny CDN", CDN, header={"server": r"^BunnyCDN"}, src=r"b-cdn\.net")
T("KeyCDN", CDN, header={"server": r"^keycdn-engine$"}, src=r"kxcdn\.com")
T("jsDelivr", CDN, src=r"cdn\.jsdelivr\.net")
T("unpkg", CDN, src=r"unpkg\.com")
T("cdnjs", CDN, src=r"cdnjs\.cloudflare\.com")
T("Google Fonts", CDN, src=r"fonts\.googleapis\.com|fonts\.gstatic\.com")
T("Cloudinary", CDN, src=r"res\.cloudinary\.com")
T("imgix", CDN, src=r"\.imgix\.net")
T("nginx", SERVER, header={"server": r"nginx"})
T("Apache HTTP Server", SERVER, header={"server": r"Apache"})
T("Microsoft IIS", SERVER, header={"server": r"Microsoft-IIS"})
T("LiteSpeed", SERVER, header={"server": r"LiteSpeed", "x-litespeed-cache": ""})
T("Varnish", SERVER, header={"x-varnish": "", "via": r"varnish"})
T("PHP", SERVER, header={"x-powered-by": r"PHP/?"}, cookie=r"^PHPSESSID$")
T("ASP.NET", SERVER, header={"x-powered-by": r"ASP\.NET", "x-aspnet-version": ""}, cookie=r"^ASP\.NET_SessionId$|^\.ASPXAUTH$")
T("Express", SERVER, header={"x-powered-by": r"^Express$"})
T("Ruby on Rails", SERVER, header={"x-runtime": r"^\d"}, meta=r"^csrf-param: authenticity_token")
T("Laravel", SERVER, cookie=r"^laravel_session$")
T("Django", SERVER, cookie=r"^csrftoken$|^django_language$", html=r"csrfmiddlewaretoken")
T("Java Servlet", SERVER, cookie=r"^JSESSIONID$")

# -- A/B testing and personalisation ------------------------------------------------------
T("Optimizely", ABTEST, src=r"cdn\.optimizely\.com|optimizely\.com/js", cookie=r"^optimizelyEndUserId$", html=r"window\.optimizely")
T("VWO", ABTEST, src=r"dev\.visualwebsiteoptimizer\.com|visualwebsiteoptimizer\.com", cookie=r"^_vwo_uuid", html=r"_vwo_code")
T("Google Optimize", ABTEST, src=r"googleoptimize\.com|optimize\.google\.com")
T("AB Tasty", ABTEST, src=r"try\.abtasty\.com|abtasty\.com", cookie=r"^ABTasty")
T("Convert", ABTEST, src=r"cdn-3\.convertexperiments\.com|convertexperiments\.com")
T("Kameleoon", ABTEST, src=r"kameleoon\.(?:eu|com)", cookie=r"^kameleoonVisitorCode$")
T("LaunchDarkly", ABTEST, src=r"app\.launchdarkly\.com|clientsdk\.launchdarkly\.com")
T("Dynamic Yield", ABTEST, src=r"cdn\.dynamicyield\.com|dynamicyield\.com")
T("Unbounce", ABTEST, src=r"unbounce\.com|ubembed\.com", html=r"data-ub-page-id")
T("Instapage", ABTEST, src=r"instapage\.com|instapagemetrics")

# -- Consent management -------------------------------------------------------------------
T("OneTrust", CONSENT, src=r"cdn\.cookielaw\.org|onetrust\.com", cookie=r"^OptanonConsent$|^OptanonAlertBoxClosed$")
T("Cookiebot", CONSENT, src=r"consent\.cookiebot\.com|cookiebot\.com", cookie=r"^CookieConsent$")
T("Didomi", CONSENT, src=r"sdk\.privacy-center\.org|didomi\.io", cookie=r"^didomi_token$")
T("Usercentrics", CONSENT, src=r"app\.usercentrics\.eu|usercentrics\.eu|usercentrics\.com", cookie=r"^uc_user_interaction$")
T("Quantcast Choice", CONSENT, src=r"quantcast\.mgr\.consensu\.org|quantcast\.com/choice", html=r"__tcfapi")
T("TrustArc", CONSENT, src=r"consent\.trustarc\.com|trustarc\.com", cookie=r"^notice_preferences$")
T("Osano", CONSENT, src=r"cmp\.osano\.com|osano\.com")
T("Iubenda", CONSENT, src=r"cdn\.iubenda\.com|iubenda\.com", html=r"_iub\.csConfiguration")
T("CookieYes", CONSENT, src=r"cdn-cookieyes\.com|cookieyes\.com", cookie=r"^cookieyes-consent$")
T("Complianz", CONSENT, src=r"/plugins/complianz-gdpr/", cookie=r"^cmplz_")
T("Axeptio", CONSENT, src=r"static\.axept\.io|axept\.io", html=r"axeptioSettings")
T("Termly", CONSENT, src=r"app\.termly\.io|termly\.io")
T("Klaro", CONSENT, src=r"klaro(?:\.min)?\.js|kiprotect\.com/klaro", cookie=r"^klaro$")
T("Cookie Information", CONSENT, src=r"policy\.app\.cookieinformation\.com|cookieinformation\.com")
T("Borlabs Cookie", CONSENT, html=r"BorlabsCookie|borlabs-cookie", src=r"/plugins/borlabs-cookie/", cookie=r"^borlabs-cookie$")
T("tarteaucitron.js", CONSENT, src=r"tarteaucitron(?:\.min)?\.js", html=r"tarteaucitron", cookie=r"^tarteaucitron$")
T("CookieFirst", CONSENT, src=r"consent\.cookiefirst\.com|cookiefirst\.com", cookie=r"^cookiefirst-consent$")
T("Sourcepoint", CONSENT, src=r"cdn\.privacy-mgmt\.com|sourcepoint\.mgr\.consensu\.org")
T("Civic Cookie Control", CONSENT, src=r"cc\.cdn\.civiccomputing\.com", cookie=r"^CookieControl$")

# -- Email providers (from MX, SPF and TXT records) ---------------------------------------
T("Google Workspace", EMAIL, dns=[("mx", r"(?:aspmx\.l\.google\.com|googlemail\.com|smtp\.google\.com)\.?$"), ("txt", r"include:_spf\.google\.com")])
T("Microsoft 365", EMAIL, dns=[("mx", r"mail\.protection\.outlook\.com\.?$"), ("txt", r"include:spf\.protection\.outlook\.com|^MS=ms\d+")])
T("Proton Mail", EMAIL, dns=[("mx", r"(?:mail|mailsec)\.protonmail\.ch\.?$"), ("txt", r"include:_spf\.protonmail\.ch|^protonmail-verification=")])
T("Zoho Mail", EMAIL, dns=[("mx", r"mx[0-9]*\.zoho\.(?:com|eu|in)\.?$"), ("txt", r"include:zoho\.(?:com|eu)|zoho-verification=")])
T("Mimecast", EMAIL, dns=[("mx", r"mimecast(?:\.com|-offshore\.com)\.?$"), ("txt", r"include:[a-z0-9.-]*mimecast")])
T("Proofpoint", EMAIL, dns=[("mx", r"pphosted\.com\.?$|ppe-hosted\.com\.?$"), ("txt", r"include:[a-z0-9.-]*pphosted\.com")])
T("Barracuda Email Security", EMAIL, dns=[("mx", r"barracudanetworks\.com\.?$|cudasvc\.com\.?$")])
T("OVHcloud Email", EMAIL, dns=[("mx", r"mail\.ovh\.net\.?$|mx\d*\.ovh\.(?:net|com)\.?$"), ("txt", r"include:mx\.ovh\.com")])
T("Gandi Mail", EMAIL, dns=[("mx", r"mail\.gandi\.net\.?$"), ("txt", r"include:_mailcust\.gandi\.net")])
T("IONOS Mail", EMAIL, dns=[("mx", r"mx\d*\.(?:kundenserver\.de|ionos\.(?:com|fr|de|es|it|co\.uk))\.?$|mx\d*\.1and1\.(?:com|fr)\.?$"), ("txt", r"include:_spf\.perfora\.net|include:_spf-eu\.ionos\.com")])
T("Fastmail", EMAIL, dns=[("mx", r"messagingengine\.com\.?$"), ("txt", r"include:spf\.messagingengine\.com")])
T("Rackspace Email", EMAIL, dns=[("mx", r"emailsrvr\.com\.?$"), ("txt", r"include:emailsrvr\.com")])
T("Infomaniak Mail", EMAIL, dns=[("mx", r"infomaniak\.ch\.?$"), ("txt", r"include:spf\.infomaniak\.ch")])
T("Amazon SES", EMAIL, dns=[("mx", r"inbound-smtp\.[a-z0-9-]+\.amazonaws\.com\.?$"), ("txt", r"include:amazonses\.com")])
T("SendGrid", EMAIL, dns=[("txt", r"include:sendgrid\.net")])
T("Mailgun", EMAIL, dns=[("mx", r"mailgun\.org\.?$"), ("txt", r"include:mailgun\.org")])
T("Mailchimp Transactional", EMAIL, dns=[("txt", r"include:spf\.mandrillapp\.com|include:servers\.mcsv\.net")])
T("Postmark", EMAIL, dns=[("txt", r"include:spf\.mtasv\.net")])
T("SparkPost", EMAIL, dns=[("txt", r"include:sparkpostmail\.com")])
T("Brevo mail", EMAIL, dns=[("txt", r"include:(?:spf\.brevo\.com|spf\.sendinblue\.com)")])
T("Mailjet", EMAIL, dns=[("txt", r"include:spf\.mailjet\.com")])
T("HubSpot email", EMAIL, dns=[("txt", r"include:[a-z0-9.-]*hubspot(?:email)?\.(?:com|net)")])
T("Salesforce email", EMAIL, dns=[("txt", r"include:_spf\.salesforce\.com|include:[a-z0-9.-]*exacttarget\.com")])
T("Zendesk email", EMAIL, dns=[("txt", r"include:mail\.zendesk\.com")])
T("Intercom email", EMAIL, dns=[("txt", r"include:_spf\.intercom\.io")])
T("Freshdesk email", EMAIL, dns=[("txt", r"include:email\.freshdesk\.com")])
T("Klaviyo email", EMAIL, dns=[("txt", r"include:_spf\.klaviyo\.com")])

# -- Business tools shown by domain-verification TXT records ------------------------------
T("Google Search Console", TOOLS, dns=[("txt", r"^google-site-verification=")])
T("Atlassian", TOOLS, dns=[("txt", r"^atlassian-domain-verification=")])
T("Meta Business", TOOLS, dns=[("txt", r"^facebook-domain-verification=")])
T("DocuSign", TOOLS, dns=[("txt", r"^docusign=")])
T("Stripe account", TOOLS, dns=[("txt", r"^stripe-verification=")])
T("Zoom", TOOLS, dns=[("txt", r"^ZOOM_verify_")])
T("Dropbox", TOOLS, dns=[("txt", r"^dropbox-domain-verification=")])
T("Notion", TOOLS, dns=[("txt", r"^notion-domain-verification=")])
T("Apple Business", TOOLS, dns=[("txt", r"^apple-domain-verification=")])
T("Adobe", TOOLS, dns=[("txt", r"^adobe-idp-site-verification=|^adobe-sign-verification=")])
T("GlobalSign", TOOLS, dns=[("txt", r"^globalsign-domain-verification=")])
T("Slack", TOOLS, dns=[("txt", r"^slack-domain-verification=")])

# fmt: on


def rule_count() -> int:
    return len({r.name for r in RULES})


def as_table() -> list[dict[str, Any]]:
    """Rules as plain dicts (for docs and tests)."""
    return [{"name": r.name, "category": r.category} for r in RULES]
