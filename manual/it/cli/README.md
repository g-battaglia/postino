# Manuale CLI di Postino

Per sviluppatori e amministratori di sistema che preferiscono il terminale, Postino include una potente Interfaccia a Riga di Comando (`postino <comando>`).

Ogni operazione fondamentale che puoi eseguire nella dashboard web può essere fatta anche tramite terminale. È perfetto per scrivere script di automazione o integrare Postino con il tuo backend principale.

## Comandi Comuni

- `postino version`: Controlla la versione attuale installata.
- `postino config validate`: Verifica che il tuo `config.toml` sia corretto.

### Gestione Iscritti
- `postino subscribers list --json`: Visualizza tutti i tuoi iscritti (output in formato JSON per facile parsing).
- `postino subscribers health --below 30`: Trova tutti gli utenti con un health score sotto a 30.
- `postino sync run`: Sincronizza il database utenti della tua applicazione esterna con Postino.

### Campagne e Analisi
- `postino campaigns list`: Vedi le tue campagne attive e passate.
- `postino analytics overview --days 30 --json`: Ottieni statistiche sulla crescita del pubblico e il coinvolgimento negli ultimi 30 giorni.

### Audit GDPR
- `postino gdpr audit user@example.com`: Estrae istantaneamente il log completo e non modificabile del consenso e delle disiscrizioni per un utente specifico.

## Attività in Background (Cron)
Utilizzerai anche i comandi di gestione di Django per eseguire i lavori in background tramite Cron:
```bash
python manage.py check_scheduled_campaigns
python manage.py evaluate_sequences
python manage.py compute_health_scores
python manage.py sync_source
```
