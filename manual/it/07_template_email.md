# 7. Template Email

I template ti permettono di definire l'aspetto e la struttura grafica delle tue email, così non dovrai ridisegnarle da zero ogni volta. Nella Dashboard Web c'è un editor apposito per creare e visualizzare in anteprima i template.

## Personalizzazione (Variabili)
Puoi rendere le tue email più personali utilizzando variabili dinamiche. Ad esempio, scrivendo:
> `Ciao {{ subscriber.name }},`

Postino sostituirà automaticamente quel testo con il vero nome di ogni singolo iscritto.

## Link Obbligatori
Per assicurarsi che tu non violi le regole del marketing via email o che i tuoi messaggi non finiscano nello spam, Postino impone l'inserimento di determinati link nei tuoi template.
Ad esempio, devi includere un link di disiscrizione (`{{ unsubscribe_url }}`) nei template di base. Se ti dimentichi di farlo, l'editor ti avviserà.

## Generazione Automatica di Testo Semplice (Plain Text)
Postino genera automaticamente una versione pulita in formato "solo testo" dai tuoi template HTML. Questo garantisce che gli utenti con client email che non supportano l'HTML, o con impostazioni di sicurezza rigorose, possano comunque leggere i tuoi messaggi.
