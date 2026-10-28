# F1 Live Timing

Dashboard per la Formula 1 che mostra in un'unica schermata il cronometraggio live e, quando non c'è nessuna sessione in corso, i risultati e le classifiche.

Il progetto include:

- **App desktop** (macOS, Windows, Linux) in Python con PySide6

> Progetto non ufficiale, non affiliato a Formula 1 né ai suoi partner. Non è pensato per uso commerciale.

## Funzionalità

### Durante una sessione (prove, qualifiche, sprint, gara)

- Classifica in tempo reale con posizione, distacco dal pilota davanti, gomma ed età della gomma, numero di soste
- Ultimo giro e miglior giro valido, con i giri di ingresso e uscita dai box segnati e esclusi dal confronto
- Tempi dei tre settori colorati: viola per il record assoluto, verde per il record personale, giallo per gli altri
- Evidenziazione quando un pilota migliora il record personale o guadagna/perde posizioni
- Bandiera in vigore (verde, gialla, safety car, VSC, rossa) e tempo rimanente
- Foto dei piloti e colori delle squadre
- Colonna dei comunicati della direzione gara

### Quando non c'è nessuna sessione in corso

- Risultato ufficiale dell'ultima gara
- Campionato piloti e costruttori
- Prossimo appuntamento del weekend (prove libere, qualifiche, sprint, gara) con orario locale e conto alla rovescia

## Fonti dei dati

- **Cronometraggio live**: feed ufficiale del cronometraggio F1, letto tramite [FastF1](https://github.com/theOehrly/Fast-F1).
- **Classifiche e calendario**: [Jolpica-F1 API](https://github.com/jolpica/jolpica-f1), servizio pubblico e gratuito.

Alcuni dati, come la mappa del circuito in movimento e la telemetria (velocità, acceleratore, freno), richiedono un abbonamento F1 TV o OpenF1 sponsor. Questo progetto **non aggira** tali restrizioni: quei dati non sono disponibili senza un abbonamento valido.

## Requisiti

- Python 3.10 o successivo
- Una connessione internet

## Installazione

```bash
git clone https://github.com/pippo-05/f1-live.git
cd f1-live
python3 -m pip install PySide6 fastf1 certifi
```

## Avvio

**App desktop, con dati simulati** (funziona sempre, utile per provare l'interfaccia):

```bash
python3 app.py
```

**App desktop, con dati live** (richiede una sessione in corso per i tempi):

```bash
python3 app.py --live
```

Aggiungi `--debug` per stampare nel terminale i primi messaggi ricevuti da ogni topic del feed.

**Su macOS** puoi usare gli script `Avvia F1 - Dati Live.command` e `Avvia F1 - Simulazione.command`, che installano le dipendenze al primo avvio e chiudono il Terminale all'uscita.


## Struttura del progetto

| File | Descrizione |
|---|---|
| `app.py` | App desktop: interfaccia, timing live, comunicati |
| `dashboard.py` | Dashboard mostrata senza sessione in corso |
| `live.py` | Lettura e decodifica del feed live, storico per pilota |
| `standings.py` | Classifiche, risultato ultima gara e calendario (Jolpica) |
| `Dockerfile`, `requirements.txt` | Installazione del server online |

## Risoluzione dei problemi

**Errore `CERTIFICATE_VERIFY_FAILED`** (su Mac con Python installato da python.org): esegui `python3 -m pip install --upgrade certifi`. Il codice imposta automaticamente il certificato.

**La finestra live è vuota** (nessun dato): il feed invia i dati solo durante una sessione in corso. Fuori sessione, l'app mostra la dashboard.

**Mancano mappa o telemetria**: questi dati richiedono un abbonamento. Vedi la sezione Fonti dei dati.
