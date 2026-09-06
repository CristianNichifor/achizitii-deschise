# Corrections and right of reply
### Corectări și drept la replică

*English below / Versiunea în engleză mai jos.*

---

## Română

### Ce publicăm și ce nu publicăm

Acest proiect publică date despre achiziții publice preluate din surse oficiale
(SEAP / e-licitatie.ro și data.gov.ro), împreună cu **indicatori** calculați din ele.

Un indicator semnalează un **tipar care merită verificat**. Nu este o acuzație, nu
constată o încălcare a legii și nu stabilește vinovăția nimănui. Fiecare indicator are
o bază legală declarată și explicații despre ce nu poate demonstra — vezi
[`METHODOLOGY.md`](METHODOLOGY.md).

Fiecare tipar semnalat aici are explicații nevinovate plauzibile. O concentrare a
valorilor sub un plafon poate reflecta un buget intern real. O procedură fără anunț
prealabil este legală în situațiile prevăzute de art. 104. O pondere de 90% către un
singur furnizor poate însemna că există o singură firmă calificată în zonă.

### Cine poate cere o corectare

Oricine. Nu este nevoie să fiți parte în achiziția respectivă.

Autoritățile contractante și operatorii economici numiți în date au, în plus, **drept
la replică**: dacă ne semnalați că o interpretare este greșită sau incompletă, publicăm
poziția dumneavoastră alături de înregistrarea contestată.

### Cum cereți o corectare

Deschideți un *issue* pe GitHub folosind unul dintre formulare:

| Situație | Formular |
|---|---|
| O înregistrare conține date greșite | **Corectare date** |
| Sunteți autoritate/ofertant și contestați o interpretare | **Drept la replică** |
| O problemă sistematică (o coloană, un an, un indicator) | **Problemă de calitate a datelor** |

👉 https://github.com/CristianNichifor/achizitii-deschise/issues/new/choose

Dacă nu doriți să folosiți GitHub, scrieți la adresa din profilul de contact al
depozitului. Vom deschide noi *issue*-ul în numele dumneavoastră, păstrând conținutul
sesizării.

### Ce se întâmplă apoi

1. **Confirmare** — confirmăm primirea și înregistrăm sesizarea public.
2. **Verificare** — comparăm înregistrarea noastră cu sursa oficială. Fiecare rând
   păstrează `ocid`, fișierul-sursă și versiunea de procesare (`parse_version`), deci
   putem reconstitui exact de unde provine o cifră.
3. **Rezultat** — una dintre:
   - **Eroare la noi** → corectăm *în pipeline*, nu doar în fișierul publicat, astfel
     încât corectura să reziste la regenerare. Se consemnează în `CHANGELOG.md`.
   - **Eroare în sursa oficială** → marcăm înregistrarea, publicăm sesizarea și
     semnalăm problema publicatorului (ANAP / ADR).
   - **Datele sunt corecte, interpretarea este contestată** → publicăm replica
     dumneavoastră alături de înregistrare. Nu ștergem date publice corecte.

### Ce facem și ce nu facem

**Facem:**
- Corectăm erori de procesare, oricât de mici.
- Publicăm replica oricărei entități numite, needitată pe fond.
- Marcăm vizibil înregistrările contestate.
- Excludem din statistici rândurile pe care nu le putem verifica.

**Nu facem:**
- Nu ștergem date publice corecte pentru că sunt incomode.
- Nu lăsăm o cifră contestată fără mențiune.
- Nu publicăm date cu caracter personal. Numele, adresele de e-mail și telefoanele
  persoanelor de contact sunt eliminate înainte de orice publicare.

### Termene

Ne angajăm la un răspuns inițial în **5 zile lucrătoare** și la o soluționare în
**30 de zile**. Acesta este un proiect voluntar; dacă termenele nu sunt respectate,
insistați în *issue*.

---

## English

### What we publish

Procurement data from official sources (SEAP / e-licitatie.ro and data.gov.ro), plus
**indicators** computed from it.

An indicator flags a **pattern that warrants review**. It is not an accusation, does not
establish a breach of law, and does not determine anyone's culpability. Every indicator
declares its legal basis and its limits — see [`METHODOLOGY.md`](METHODOLOGY.md).

Every pattern flagged here has plausible innocent explanations, which are documented
alongside each indicator.

### Who may request a correction

Anyone. You do not need to be a party to the procurement.

Contracting authorities and suppliers named in the data additionally have a **right of
reply**: tell us an interpretation is wrong or incomplete and we publish your position
next to the record.

### How

Open a GitHub issue using one of the templates — data correction, right of reply, or
data-quality problem:

👉 https://github.com/CristianNichifor/achizitii-deschise/issues/new/choose

If you would rather not use GitHub, write to the contact address on the repository
profile and we will file it on your behalf, preserving your wording.

### What happens

1. **Acknowledge** — receipt confirmed and logged publicly.
2. **Verify** — our record is compared against the official source. Every row keeps its
   `ocid`, source file and `parse_version`, so any figure can be traced to its origin.
3. **Outcome** — one of:
   - **Our error** → fixed *in the pipeline*, not just the published file, so the fix
     survives regeneration. Logged in `CHANGELOG.md`.
   - **Source error** → the record is flagged, your report published, and the issue
     raised with the publisher (ANAP / ADR).
   - **Data correct, interpretation disputed** → your reply is published alongside the
     record. We do not remove accurate public data.

### What we will and will not do

**Will:** correct processing errors however small; publish any named entity's reply
unedited in substance; visibly mark disputed records; exclude unverifiable rows from
statistics.

**Will not:** remove accurate public data because it is unwelcome; leave a disputed
figure unannotated; publish personal data — contact names, e-mail addresses and phone
numbers are stripped before anything is published.

### Timelines

Initial response within **5 working days**, resolution within **30 days**. This is a
volunteer project; if those slip, press us in the issue.
