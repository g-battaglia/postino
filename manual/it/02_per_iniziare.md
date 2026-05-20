# 2. Per Iniziare

L'installazione e la configurazione di Postino sono pensate per essere estremamente semplici.

## Il File di Configurazione
Tutto in Postino — dalla connessione al database al provider email e alle impostazioni sulla privacy — è controllato da un unico file di configurazione chiamato `config.toml`.

Quando installi Postino per la prima volta, dovrai copiare il file fornito `config.example.toml`, rinominarlo in `config.toml` e inserire i tuoi dati specifici:
- **Chiavi segrete del server**
- **URL del database**
- **Dettagli del provider email** (es. la tua API key di Resend)
- **Regole sulla privacy** (es. richiedere il double opt-in)

## Avviare Postino
Una volta completata la configurazione, ti basterà eseguire dei semplici comandi per inizializzare il database e avviare il server:
```bash
python manage.py migrate          # Prepara il database
python manage.py createsuperuser  # Crea l'account amministratore
python manage.py runserver        # Avvia l'applicazione
```
