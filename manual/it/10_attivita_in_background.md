# 10. Attività in Background

Anche se usi principalmente la Dashboard Web, Postino ha bisogno di eseguire dei compiti automatici dietro le quinte.

Postino evita deliberatamente code di task complesse. Si basa invece su semplici **Cron Job** (attività programmate che il tuo amministratore di sistema configura sul server).

## Cosa succede in background?
Mentre tu scrivi email e analizzi le statistiche, il server si occupa automaticamente di:
- **Inviare Campagne Programmate:** Controlla se hai programmato una campagna per quest'ora e la invia al posto tuo.
- **Far Avanzare le Sequenze:** Verifica chi deve ricevere l'email successiva della sequenza di Onboarding.
- **Aggiornare l'Health Score:** Ricalcola i punteggi in base alle interazioni della giornata.

Tu non devi preoccuparti di queste operazioni: una volta che l'amministratore ha installato Postino, tutto funziona in modo invisibile mentre tu usi comodamente la dashboard.
