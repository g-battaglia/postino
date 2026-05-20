# 7. Email Templates

Templates allow you to define the look and feel of your emails so you don't have to design them from scratch every time.

## Personalization (Variables)
You can make your emails feel personal by using dynamic variables. For example, if you write:
> `Hello {{ subscriber.name }},`

Postino will automatically replace that with the actual name of each subscriber.

## Required Links
To ensure you never violate email marketing rules or get marked as spam, Postino enforces the inclusion of specific links in your templates. 
You must include an Unsubscribe link (`{{ unsubscribe_url }}`) in your base templates. If you forget, the system will warn you.

## Plain Text Auto-generation
Postino automatically generates a clean plain-text version of your HTML templates. This ensures that users with text-only email clients, or users with strict security settings, can still read your messages.
