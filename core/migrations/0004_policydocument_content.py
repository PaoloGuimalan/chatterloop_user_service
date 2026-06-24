from django.db import migrations, models


TERMS_CONTENT = """
<h1>Chatterloop &mdash; Terms and Conditions</h1>
<p class="updated"><strong>Last Updated: June 22, 2026</strong></p>

<p>Welcome to Chatterloop ("Chatterloop," "we," "us," or "our"). These Terms and Conditions ("Terms") govern your access to and use of the Chatterloop website, web application, mobile applications, and related services (collectively, the "Service"). By creating an account or using the Service, you agree to be bound by these Terms. If you do not agree, do not use the Service.</p>

<h2>1. Eligibility</h2>
<p>You must be at least 13 years old to use Chatterloop. By creating an account, you represent that you meet this requirement and that the information you provide (including your birthdate) is accurate. If we learn that a user is under the minimum age, we may suspend or terminate that account.</p>

<h2>2. Your Account</h2>
<ul>
  <li>You are responsible for maintaining the confidentiality of your login credentials and for all activity that occurs under your account.</li>
  <li>You may register using an email address and password, or via third-party sign-in (e.g., Google). You agree to provide accurate profile information (name, username, email, birthdate, gender, profile photo, etc.).</li>
  <li>You must notify us promptly of any unauthorized use of your account.</li>
  <li>We reserve the right to suspend or terminate accounts that violate these Terms, provide false information, or are used for impersonation.</li>
</ul>

<h2>3. Description of the Service</h2>
<p>Chatterloop is a social communication platform that allows users to:</p>
<ul>
  <li>Send direct and group <strong>messages</strong>, including text, media, replies, reactions, and read receipts.</li>
  <li>Make <strong>voice and video calls</strong> and share screens with other users in real time.</li>
  <li>Create and view <strong>posts</strong> on a social feed, with adjustable privacy settings (public, friends-only, or restricted).</li>
  <li>Join or create <strong>servers/realms</strong> &mdash; topic- or community-based group spaces with channels, member roles, and administrative permissions.</li>
  <li>Use <strong>location- and map-based features</strong>, including location-tagged content and routing/map services.</li>
  <li>Receive <strong>push and in-app notifications</strong> about activity relevant to your account.</li>
  <li>Use <strong>AI-assisted features</strong>, such as suggested replies and conversational assistance, powered by third-party AI providers.</li>
</ul>
<p>We may add, modify, or remove features at any time without prior notice.</p>

<h2>4. User Content</h2>
<p>"User Content" means any text, images, video, audio, location data, or other material you submit, post, or transmit through the Service (including posts, messages, profile photos, and server/channel content).</p>
<ul>
  <li><strong>You retain ownership</strong> of your User Content. By submitting it, you grant Chatterloop a worldwide, non-exclusive, royalty-free license to host, store, reproduce, transmit, and display that content solely as necessary to operate, provide, and improve the Service.</li>
  <li>You are solely responsible for your User Content and for ensuring you have the rights to share it.</li>
  <li>You agree not to post content that is illegal, infringing, defamatory, harassing, hateful, sexually exploitative (especially involving minors), violent, or that violates the privacy or rights of others.</li>
  <li>Content shared in messages or posts may be visible to other users depending on your privacy settings and the settings of servers/realms you join. Chatterloop is not responsible for content shared by other users.</li>
  <li>We may, but are not obligated to, monitor, review, or remove User Content that we believe violates these Terms, applicable law, or community standards. We may suspend or terminate accounts responsible for such content.</li>
  <li>You may delete your own messages and posts; deletion removes the content from standard views, but residual copies may persist in backups or as a result of others having already viewed/saved the content.</li>
</ul>

<h2>5. AI-Assisted Features</h2>
<p>Chatterloop offers AI-generated suggestions (e.g., reply assistance) powered by third-party large language model providers. You acknowledge that:</p>
<ul>
  <li>AI-generated content may be inaccurate, inappropriate, or unexpected, and is provided "as-is."</li>
  <li>Content you input into AI-assisted features may be transmitted to and processed by third-party AI providers in order to generate suggestions.</li>
  <li>You should not rely on AI-generated output as professional, legal, medical, or financial advice.</li>
</ul>

<h2>6. Communications, Calls, and Real-Time Features</h2>
<ul>
  <li>Voice and video calling, screen sharing, and real-time messaging are provided to facilitate communication between users. Chatterloop does not record, monitor, or store the live audio/video content of your calls beyond what is technically required to establish and route the connection, except as disclosed in our Privacy Policy.</li>
  <li>You agree not to use calling, messaging, or screen-sharing features to harass, threaten, defraud, or transmit unlawful content to other users.</li>
</ul>

<h2>7. Servers, Realms, and Communities</h2>
<ul>
  <li>Users who create a server/realm act as administrators for that space and are responsible for moderating content and managing membership within it, subject to these Terms.</li>
  <li>Chatterloop may remove servers/realms, channels, or members that violate these Terms or applicable law.</li>
  <li>Administrative permissions granted within a server/realm do not override Chatterloop's own enforcement rights over the Service as a whole.</li>
</ul>

<h2>8. Location Data</h2>
<p>If you enable location-based features (such as map feeds or location-tagged posts), you consent to the collection and processing of your approximate or precise location for the purpose of providing those features. You may disable location sharing at any time through your device or account settings, which may limit access to location-based features.</p>

<h2>9. Third-Party Services</h2>
<p>The Service integrates with third-party providers for functions such as authentication (e.g., Google Sign-In), cloud storage and media hosting, mapping and routing, email delivery, and AI processing. Your use of those integrations may also be subject to the relevant third party's own terms and privacy policies. Chatterloop is not responsible for the acts or omissions of third-party providers.</p>

<h2>10. Acceptable Use</h2>
<p>You agree not to:</p>
<ul>
  <li>Use the Service for any unlawful purpose or in violation of any applicable local, national, or international law.</li>
  <li>Upload viruses, malware, or attempt to gain unauthorized access to the Service, other accounts, or our systems.</li>
  <li>Scrape, harvest, or collect data about other users without consent.</li>
  <li>Impersonate any person or entity, or misrepresent your affiliation with any person or entity.</li>
  <li>Use automated means (bots, scripts) to access or interact with the Service without our prior written permission.</li>
  <li>Interfere with or disrupt the integrity or performance of the Service, including its messaging, calling, or media infrastructure.</li>
  <li>Send spam, unsolicited advertising, or engage in phishing through messages, posts, or servers/realms.</li>
</ul>

<h2>11. Intellectual Property</h2>
<p>The Service, including its software, design, graphics, logos, and trademarks (excluding User Content), is owned by Chatterloop or its licensors and is protected by intellectual property laws. You may not copy, modify, distribute, or create derivative works of the Service except as expressly permitted.</p>

<h2>12. Privacy</h2>
<p>Our collection and use of personal information (including account details, messages, posts, location data, and device information) is described in our Privacy Policy, which is incorporated into these Terms by reference. By using the Service, you consent to such collection and use.</p>

<h2>13. Termination</h2>
<p>We may suspend or terminate your access to the Service at any time, with or without notice, for conduct that we believe violates these Terms, harms other users, harms Chatterloop, or for any other reason at our discretion. You may stop using the Service and delete your account at any time. Provisions that by their nature should survive termination (including Sections 4, 11, 14, and 15) will continue to apply.</p>

<h2>14. Disclaimers</h2>
<p>THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE," WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS OR IMPLIED, INCLUDING WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT, OR THAT THE SERVICE WILL BE UNINTERRUPTED, SECURE, OR ERROR-FREE. CHATTERLOOP DOES NOT GUARANTEE THE ACCURACY, COMPLETENESS, OR RELIABILITY OF ANY USER CONTENT OR AI-GENERATED CONTENT.</p>

<h2>15. Limitation of Liability</h2>
<p>TO THE MAXIMUM EXTENT PERMITTED BY LAW, CHATTERLOOP AND ITS OFFICERS, EMPLOYEES, AND AFFILIATES SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF DATA, PROFITS, OR GOODWILL, ARISING FROM YOUR USE OF OR INABILITY TO USE THE SERVICE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.</p>

<h2>16. Indemnification</h2>
<p>You agree to indemnify and hold harmless Chatterloop and its officers, employees, and affiliates from any claims, damages, liabilities, and expenses (including legal fees) arising out of your use of the Service, your User Content, or your violation of these Terms.</p>

<h2>17. Changes to These Terms</h2>
<p>We may update these Terms from time to time. If we make material changes, we will notify you through the Service or by other reasonable means. Continued use of the Service after changes take effect constitutes acceptance of the revised Terms.</p>

<h2>18. Governing Law</h2>
<p>These Terms are governed by the laws of [Jurisdiction to be specified], without regard to conflict-of-law principles. Any disputes arising from these Terms or the Service shall be resolved in the courts of that jurisdiction, unless otherwise required by applicable law.</p>

<h2>19. Contact Us</h2>
<p>If you have questions about these Terms, please contact us at [support email to be specified].</p>
""".strip()


PRIVACY_CONTENT = """
<h1>Chatterloop &mdash; Privacy Policy</h1>
<p class="updated"><strong>Last Updated: June 22, 2026</strong></p>

<p>This Privacy Policy explains what personal information Chatterloop ("Chatterloop," "we," "us," or "our") collects, how we use and share it, and the rights you have over it. It applies to the Chatterloop website, web application, and mobile applications (collectively, the "Service"). It should be read together with our <strong>Terms and Conditions</strong>.</p>

<h2>1. Information We Collect</h2>
<p><strong>Account information.</strong> When you register (directly or via Google Sign-In), we collect your name, username, email address, birthdate, gender, password (stored as a one-way hash, never in plain text), and profile/cover photos you choose to upload.</p>
<p><strong>Content you create.</strong> Posts, captions, comments, reactions, direct and group messages, diary entries, and any media (images/video) you attach to them.</p>
<p><strong>Connections and community activity.</strong> Your contacts/connections, the servers/realms ("communities") you create, join, or follow, and invitations you send or receive (including the email address of someone you invite who isn't yet a Chatterloop user).</p>
<p><strong>Location data.</strong> If you enable location-based features (map feed, location-tagged posts), we collect your approximate or precise location for the purpose of providing those features.</p>
<p><strong>Device and usage information.</strong> Device type, browser, operating system, IP address, device identifiers, and session/login activity. We also log engagement activity (views, searches, profile visits) to operate features like the feed and ranking.</p>
<p><strong>Information from third parties.</strong> If you sign in with Google, we receive your name and email address from Google as part of that sign-in. Your use of Google Sign-In is also governed by Google's own privacy policy.</p>
<p><strong>Reports and moderation records.</strong> If you report another user or piece of content, we record the report (who filed it, who/what it concerns, the reason, and any details you provide).</p>
<p><strong>Consent records.</strong> When you accept this Privacy Policy or our Terms and Conditions, we record the version accepted, the date and time, and the IP address and browser/device information associated with that acceptance, as proof of consent.</p>

<h2>2. How We Use Information</h2>
<p>We use the information above to:</p>
<ul>
  <li>Create and maintain your account, and verify you meet our minimum age requirement (13+).</li>
  <li>Operate core features: messaging, voice/video calls, the social feed, servers/realms, location/map features, and notifications.</li>
  <li>Power AI-assisted features (see Section 3).</li>
  <li>Maintain safety and trust: respond to reports, enforce blocks, and act on violations of our Terms.</li>
  <li>Maintain and improve the Service, including ranking and recommendations in the feed.</li>
  <li>Communicate with you about your account (e.g., email verification, security notices).</li>
  <li>Comply with legal obligations and enforce our Terms and Conditions.</li>
</ul>

<h2>3. AI-Assisted Features</h2>
<p>Chatterloop offers AI-generated suggestions (such as reply assistance) powered by third-party large language model providers, currently <strong>OpenAI</strong> and <strong>Groq</strong>. When you use these features, the relevant text you're composing or replying to is sent to these providers to generate a suggestion. We do not control how these providers process data beyond what's described in their own privacy policies and our agreements with them; we recommend avoiding entering sensitive personal information into AI-assisted fields.</p>

<h2>4. Messaging and Calls</h2>
<p>Direct and group messages are stored so that conversations remain available to their participants, and so that read receipts and reactions work. Voice and video calls (powered by WebRTC/mediasoup) are routed in real time; <strong>we do not record or store the audio/video content of your calls</strong> beyond what is technically required to establish and maintain the connection (e.g., session/signaling metadata).</p>

<h2>5. Location Data</h2>
<p>Location data is only collected if you enable a location-based feature. You can disable location sharing at any time through your device or in-app settings, which may limit access to those features. We use third-party mapping/routing providers (such as OpenRouteService and LocationIQ) to power these features, which may process the location data you submit.</p>

<h2>6. Who We Share Information With</h2>
<p>We share personal information with:</p>
<ul>
  <li><strong>Other users</strong>, according to your privacy settings (e.g., a public post is visible to anyone; a private post is not) and your realm/server memberships.</li>
  <li><strong>Service providers</strong> who process data on our behalf, including:
    <ul>
      <li><strong>Google</strong> (Sign-In/OAuth)</li>
      <li><strong>Firebase</strong>, <strong>AWS</strong>, and <strong>DigitalOcean Spaces</strong> (media and file storage)</li>
      <li><strong>OpenAI</strong> and <strong>Groq</strong> (AI-assisted reply suggestions)</li>
      <li><strong>OpenRouteService</strong> and <strong>LocationIQ</strong> (location/routing features)</li>
      <li>Our email delivery provider (account verification and notices)</li>
    </ul>
  </li>
  <li><strong>Legal and safety purposes</strong>, where required by law, to enforce our Terms, or to protect the rights, safety, or property of Chatterloop, our users, or the public.</li>
</ul>
<p>We do not sell your personal information.</p>

<h2>7. Cookies, Local Storage, and Similar Technologies</h2>
<p>Our web application stores a session token and a device identifier in your browser's local storage to keep you signed in and to recognize your device for security purposes (e.g., detecting new device logins). We do not currently use third-party advertising cookies.</p>

<h2>8. Data Retention</h2>
<p>We retain your account and content data for as long as your account is active. If you delete your account, we anonymize your profile (scrubbing your name, email, username, and other identifying fields) rather than deleting every record outright, because other users' conversations, comments, and communities may depend on that content remaining intact and attributable to a placeholder identity. See Section 9 for details on what deletion actually does. Consent records are retained as a compliance audit trail even after account deletion.</p>

<h2>9. Your Rights</h2>
<p>Depending on your location, you may have rights under laws such as the GDPR (EU/UK), CCPA (California), LGPD (Brazil), and similar laws elsewhere, including the right to:</p>
<ul>
  <li><strong>Access your data.</strong> From Settings &rarr; Data &amp; Privacy, you can download a copy of the personal data we hold about you, including your profile, posts, comments, diary entries, realm memberships, messages you've sent, and consent history.</li>
  <li><strong>Delete your account.</strong> From the same settings page, you can permanently deactivate your account. This anonymizes your identifying information (name, email, username, password) immediately and signs you out everywhere; your own private content (e.g., diary entries) is deleted, while posts/comments visible to others are soft-deleted in place to preserve other users' conversations.</li>
  <li><strong>Correct your information.</strong> You can update your profile information at any time from your account settings.</li>
  <li><strong>Withdraw consent.</strong> Where we rely on your consent (e.g., for location features), you can withdraw it at any time through your device or in-app settings; this may limit your ability to use the related feature.</li>
  <li><strong>Object to or restrict certain processing</strong>, where applicable law provides for it.</li>
</ul>
<p>To exercise a right not covered by the in-app tools above, contact us using the information in Section 13.</p>

<h2>10. Children's Privacy</h2>
<p>Chatterloop requires all users to be at least 13 years old, and we verify a birthdate is provided and meets this minimum before an account becomes usable. We do not knowingly collect personal information from children under 13. If we become aware that we have collected personal information from a child under 13, we will take steps to delete it.</p>

<h2>11. Data Security</h2>
<p>We use industry-standard measures to protect your information, including password hashing, encrypted connections, and access controls on our infrastructure. No system is completely secure, and we cannot guarantee absolute security of information transmitted to or stored by the Service.</p>

<h2>12. International Data Transfers</h2>
<p>Chatterloop is intended to be accessible globally. Depending on where you and our service providers are located, your information may be transferred to, stored in, and processed in countries other than your own, which may have different data protection laws. Where required, we take steps intended to ensure your information receives an adequate level of protection in the jurisdictions in which we process it.</p>

<h2>13. Contact Us</h2>
<p>If you have questions about this Privacy Policy or wish to exercise a privacy right not available through in-app tools, contact us at [support/privacy email to be specified].</p>

<h2>14. Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. When we make a material change, we will publish a new version and require you to review and accept it again before continuing to use the Service, the same way you accepted this version.</p>
""".strip()


def populate_content(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    # Policy bodies now live entirely in the DB as rich text; the old static
    # files (/terms.html, /privacy.html) are gone, so clear the dead
    # document_url on the seeded rows too.
    PolicyDocument.objects.filter(document_type="terms", version="1.0").update(
        content=TERMS_CONTENT, document_url=""
    )
    PolicyDocument.objects.filter(document_type="privacy", version="1.0").update(
        content=PRIVACY_CONTENT, document_url=""
    )


def clear_content(apps, schema_editor):
    PolicyDocument = apps.get_model("core", "PolicyDocument")
    PolicyDocument.objects.filter(document_type="terms", version="1.0").update(
        content="", document_url="/terms.html"
    )
    PolicyDocument.objects.filter(document_type="privacy", version="1.0").update(
        content="", document_url="/privacy.html"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_seed_privacy_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="policydocument",
            name="content",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="policydocument",
            name="document_url",
            field=models.CharField(blank=True, default="", max_length=500),
        ),
        migrations.RunPython(populate_content, clear_content),
    ]
