# Follow-up to ADR — draft, not yet sent

The first request went out **2026-09-08** to `contact@adr.gov.ro`, copied to
`contact.companii@e-licitatie.ro`. See
[the incident report](incident-2026-09-07-seap-block.md#request-sent-2026-09-08) for what
it asked and why.

**Do not send this before roughly 2026-09-15.** A week is the shortest interval at which a
reminder to a public body reads as diligence rather than pressure, and the original is
hours old at the time of writing.

## What has changed since the original, and why it is worth saying

One thing, and it is the reason a follow-up is worth sending at all rather than just
waiting: the original said automated collection would continue from GitHub runners, which
were never blocked, at a rate inside the published ceiling. **That is no longer true.** All
automated collection has been suspended since 2026-09-08 — including the compliant
scheduled job — and nothing contacts SEAP now unless a person starts it by hand.

That is worth reporting for one reason only: it is a fact about our behaviour that they
would otherwise have no way to verify, and it removes the most obvious reason to treat the
request as self-serving. It is **not** offered as leverage, and the letter must not ask for
anything in return for it.

## Draft

> **Subiect:** Revenire — solicitare acces date achiziții publice (SEAP), ref. mesajul din
> 8 septembrie 2026
>
> Bună ziua,
>
> Revin la mesajul trimis în 8 septembrie 2026 privind accesul la datele publice din SEAP
> pentru proiectul open-source `achizitii-deschise`
> (https://github.com/CristianNichifor/achizitii-deschise), care publică prețuri unitare
> din achizițiile directe, sub licență deschisă.
>
> Între timp am suspendat complet colectarea automată, inclusiv rularea programată care
> se încadra în limitele publicate de 500 de accesări la 5 minute. În acest moment nimic
> nu contactează SEAP fără ca o persoană să pornească manual procesul. Menționez acest
> lucru ca informație, nu ca argument: mi s-a părut corect să știți că am oprit inclusiv
> ceea ce era permis, cât timp întrebarea este deschisă.
>
> Cele trei întrebări din mesajul inițial rămân:
>
> 1. Poate fi ridicată restricția pentru adresa afectată?
> 2. Există un export în masă care să includă liniile de comandă — cantitate și preț
>    unitar? Sunt câmpurile pe care niciun export de pe data.gov.ro nu le conține, și
>    singurele pentru care interogăm SEAP direct. Dacă există contra cost sau la cerere,
>    ne interesează.
> 3. Dacă nu există, există o procedură de înregistrare sau de includere pe o listă
>    pentru reutilizatorii de date deschise, și ce ritm de colectare ați considera
>    acceptabil?
>
> Dacă răspunsul la toate trei este nu, vă rog să îmi spuneți: e un răspuns util și
> închide subiectul fără alte reveniri din partea mea.
>
> Vă mulțumesc pentru timpul acordat,
>
> Cristian Nichifor
> cristian@cnwebify.com

## Notes on what the draft deliberately does not do

- **It does not ask for a higher rate as a favour.** The original made that choice and it
  still holds. A rate granted informally is a rate that can be withdrawn informally.
- **It does not mention distributing traffic across addresses.** That was raised twice
  during the project and declined twice: routing around a per-IP limit is evading an
  access control SEAP applied deliberately, after already blocking this project.
- **It does not repeat the apology.** The original stated the breach plainly — roughly 40
  requests a second against a published ceiling of 500 per 5 minutes, with the
  announcement read only afterwards — and recorded the fix. Restating it invites the
  conversation to be about the mistake rather than the question.
- **It offers an easy no.** A public body that can close a request in one line is more
  likely to answer than one that must negotiate.

## If there is still no reply

Do not send a third message. The daily job stays held or is resumed — that is a decision
about our own conduct, not about their silence, and it should be taken on its own terms.
The relevant facts for that decision are in the incident report: collection inside the
published limit was never itself the thing that caused the block, and every day held is a
day of unit prices that can never be recovered.
