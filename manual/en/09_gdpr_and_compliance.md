# 9. GDPR & Compliance

Compliance is Postino's defining feature. It handles the complicated parts of the GDPR so you don't have to.

## Double Opt-In
By default, new signups receive an email asking them to confirm their subscription. Until they click that link, they remain `Pending` and cannot be emailed.

## Bulletproof Unsubscribes
- **One-Click:** Unsubscribing is immediate. There are no "Are you sure?" prompts or login screens.
- **Irreversible:** Once a user unsubscribes, Postino's core code prevents them from being accidentally re-added or emailed again, even if you run a database sync.
- **Granular Consent:** Users can access a "Preference Center" to choose which *types* of emails they want (e.g., "Yes to product updates, No to marketing").

## Data Rights
Under GDPR, users have the "Right to Access" and the "Right to Erasure". 
- Postino allows users to download a complete JSON file of all data you hold on them.
- Users can click a button to trigger a full deletion of their personal data (though Postino will retain a cryptographic hash of their email just to ensure they are never emailed again).
