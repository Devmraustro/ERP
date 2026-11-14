# Guide de l'utilisateur — ERP Algérie

**Version du document :** Phase 59, `arena/63d762c8-erp`, base `098483a1f02df0308f3889c93714efab5d13b550`
**Public :** opérateurs (comptabilité, achats, magasin) — pas d'administration système.
**Principe de rédaction :** ce guide ne décrit **que** des fonctions qui existent et ont été
exécutées sur cette version. Tout ce qui n'est pas dans un écran réel est absent de ce document.

> **À lire avant toute utilisation réelle.** Ce logiciel n'a **pas** reçu de validation par un
> `Expert-Comptable` algérien. Les questions `Q1`–`Q12` sur la fiscalité (timbre, TVA, G50, comptes
> `380`/`408`) sont **en attente**. Vous pouvez travailler en données de test ou en staging ; vous ne
> pouvez pas présenter les résultats comme une comptabilité certifiée.

---

## 1. Se connecter

1. Lancer l'application. L'écran **Connexion** apparaît (titre « Connexion », sous-titre
   « Connectez-vous à votre espace de gestion »).
2. Saisir **Nom d'utilisateur** et **Mot de passe**, puis **Se connecter**.
3. En cas de refus, le message est explicite : `Connexion refusée : <détail>`.

**Comptes.** Il n'y a pas d'inscription libre. Un administrateur crée les comptes. Rôles existants :
`admin`, `accountant`, `approver`, `viewer` — chacun ne peut faire que ce que son rôle autorise.

**Verrouillage (important).** Après **5 mots de passe erronés**, le compte est verrouillé
**15 minutes**. Même le bon mot de passe est alors refusé : ce n'est pas une panne. Attendez la fin du
délai, ou demandez à un administrateur d'utiliser **un autre compte**. Chaque verrouillage est
enregistré dans le journal d'audit (`AUTH_ACCOUNT_LOCKED`).

## 2. L'écran principal et la navigation

Menu latéral (libellés français réels) :

| Groupe | Écran | Sous-titre affiché |
|---|---|---|
| OPÉRATIONS | Tableau de bord | Vue opérationnelle du poste de travail |
| | Documents | Documents sources et état de traitement |
| | Factures fournisseurs | Cycle de vie des factures fournisseurs |
| | Fournisseurs & clients | Tiers fournisseurs et clients |
| | Articles | Catalogue des articles et coûts standard |
| | Stocks | Quantités en stock par dépôt |
| COMPTABILITÉ | Plan comptable | Structure du plan comptable SCF |
| | Balance générale | Totaux débit/crédit des écritures validées |
| ADMINISTRATION | Utilisateurs | (réservé aux administrateurs) |

Menu **Fichier** : Actualiser, Se déconnecter, Quitter. Menu **Affichage** : Thème. Menu **Langue** :
Français / العربية / English. Menu **Aide** : À propos.

**Langue et sens de lecture.** Choisir العربية bascule toute l'interface en arabe **et** le sens de
lecture de droite à gauche. Les trois langues contiennent exactement les mêmes 286 libellés : aucun
écran ne retombe partiellement en français. Le choix de langue s'applique immédiatement.

**Fenêtre.** Taille minimale **940 × 620**. En dessous, la mise en page n'est pas garantie.

## 3. Documents sources et OCR

Écran **Documents** : liste des fichiers téléversés, avec leur état de traitement.

* Un document téléversé est **analysé** (`Analysé`) puis ses champs sont extraits.
* Chaque champ extrait porte une **Confiance** et une **Origine**. La confiance peut être marquée
  « non mesurée » ou « (calibrée) » : c'est une information sur la lecture automatique, pas une
  garantie de justesse.
* **Aucun document n'est comptabilisé automatiquement.** La chaîne est toujours :
  extraction → révision humaine → soumission → approbation → comptabilisation.

### États d'une facture (libellés exacts)

| État interne | Affiché | Signification |
|---|---|---|
| `Draft` | Brouillon | créée, non validée |
| `Validated` | Validée | contrôles de forme passés, **non comptabilisée** |
| `NeedsReview` | À réviser | un contrôle a échoué (totaux, doublon, champ) |
| `PendingApproval` | En attente | soumise, attend la décision d'un approbateur |
| `Approved` | Approuvée | approuvée par un **autre** utilisateur |
| `Posted` | Comptabilisée | écriture créée, balance équilibrée |
| `Rejected` | Rejetée | refusée en approbation |
| `Reversed` | Extournée | annulée par un avoir |
| `DuplicateRejected` | Doublon rejeté | même fournisseur + même numéro |
| `CLEAN` | Analysé | document traité |

## 4. Réviser une facture (écran de révision)

Bouton **Réviser…** sur une facture.

Le panneau gauche affiche les **champs extraits (OCR)** : *Champ*, *Texte OCR d'origine*,
*Confiance*, *Origine*, *Correction humaine*. Le panneau central affiche les **lignes de facture**,
et le panneau droit l'**aperçu du document source**.

### 4.1 La bannière « Points à traiter avant soumission »

Si quelque chose bloque la soumission, une bannière liste **chaque** point, par exemple :

* `Champ requis non vérifié : amount_ttc_dzd`
* `Champ signalé : supplier_invoice_no`
* `Lignes d'extraction en double : <champ>`
* `Totaux extraits incohérents : <détail>`

Tant que la liste n'est pas vide, **la soumission est refusée**. C'est voulu : l'écran vous dit
exactement quoi corriger au lieu d'échouer sans explication.

### 4.2 Corriger un champ mal lu

1. Sélectionner le champ dans la liste.
2. Le champ **Texte OCR d'origine** montre ce que la machine a lu (par exemple `11 9OO DD`).
3. Saisir la valeur correcte dans la zone de correction, puis **Corriger…**.

Le champ garde **les deux** informations : la valeur corrigée **et** le texte OCR d'origine. C'est une
pièce justificative : la lecture automatique n'est jamais effacée.

### 4.3 Un champ « non extrait »

Si le document n'a pas permis de lire un champ, la zone de correction l'indique :
« Ce champ n'a pas été extrait : saisissez la valeur lue sur le document, puis validez. »

Saisir la valeur lue. Une mention **`[manual entry]`** apparaît ensuite dans la colonne du texte
d'origine : elle signifie « **une personne a lu cette valeur ; aucune machine ne l'a mesurée** ».
Ce n'est pas une erreur, c'est une provenance honnête.

### 4.4 Signaler / confirmer

* **Confirmer la valeur** : vous avez vérifié le champ, la valeur est bonne.
* **Signaler** : le champ est douteux ; il reste bloquant tant qu'il n'est pas traité.
* Une décision refusée affiche `Décision refusée : <détail>` — par exemple si la valeur a changé
  entre-temps (`FIELD_VALUE_CHANGED_SINCE_READ`) : relisez le champ et recommencez.

### 4.5 Soumettre, approuver, comptabiliser

1. **Soumettre** — possible seulement quand la bannière est vide. La facture passe en **En attente**.
2. **Approuver / Rejeter** — par un **autre** utilisateur autorisé. *Vous ne pouvez pas approuver une
   facture que vous avez créée* : le refus est explicite
   (`SEGREGATION_OF_DUTIES_VIOLATION`). C'est une règle comptable, pas un bug.
3. **Comptabiliser** — réservé aux rôles `accountant` / `admin`, et seulement en état **Approuvée**.
   Le message en cas de refus nomme l'état : « Cannot post invoice N in state 'NeedsReview'; requires
   'Approved' state. »
4. **Extourner** — crée un **avoir** et une écriture d'extourne numérotée (`RAVOIR/…`) ; la facture
   d'origine reste **Comptabilisée** et n'est jamais supprimée.

Chaque étape est enregistrée dans le journal d'audit.

## 5. Fournisseurs & clients

Écran **Fournisseurs & clients**. Champs contrôlés à la création :

| Champ | Règle | Message si invalide |
|---|---|---|
| NIF | 15 ou 20 chiffres | `Invalid NIF '123': must be 15 or 20 digits.` |
| NIF déjà utilisé | unique par société | `A partner with NIF … already exists in this company (…)` |
| Nom | obligatoire | message de champ requis |
| Régime fiscal | `REEL`, `IFU`, `EXEMPT`, `FOREIGN` | — |

Une erreur de saisie est **refusée et expliquée** ; elle n'est jamais enregistrée à moitié.

## 6. Articles

Écran **Articles** : SKU, nom, unité, coût standard, prix de vente, taux de TVA, et les **comptes
comptables** (stock, achat, compte d'attente).

| Champ | Règle | Message |
|---|---|---|
| SKU | unique | `An item with SKU '…' already exists (id=…)` |
| Coût standard | montant DZD ≥ 0 | erreur de format |
| Compte comptable | doit exister dans **votre** plan SCF | `stock_account_code '9999' is not an account of this company's SCF chart.` |

**Attention aux codes.** Les comptes réels du plan SCF livré sont par exemple `30` (marchandises
stockées), `380` (achats stockés — compte d'attente), `401` (fournisseurs), `4456` (TVA récupérable),
`601` (achats). **`300` n'existe pas.** Utilisez l'écran **Plan comptable** pour vérifier un code avant
de le saisir.

## 7. Stocks et balance

* Écran **Stocks** : quantités par dépôt.
* Écran **Balance générale** : totaux **débit/crédit des écritures validées**. Les écritures sont
  équilibrées par construction ; si la balance affiche un déséquilibre, **arrêtez et appelez
  l'administrateur** — ne « corrigez » rien à la main.

## 8. Erreurs fréquentes et que faire

| Message / situation | Cause | Action |
|---|---|---|
| `Connexion refusée` | identifiants faux, ou compte verrouillé | vérifier ; après 5 échecs attendre 15 min |
| Bannière « Points à traiter » | champ non vérifié, signalé, doublon, totaux incohérents | traiter **chaque** ligne listée |
| `SEGREGATION_OF_DUTIES_VIOLATION` | vous approuvez votre propre facture | faire approuver par un autre utilisateur |
| `Role 'approver' is not authorized…` | mauvais rôle pour comptabiliser | demander à un `accountant` / `admin` |
| `Cannot post invoice N in state …` | facture non approuvée | terminer la révision et l'approbation |
| `Décision refusée : FIELD_VALUE_CHANGED_SINCE_READ` | la valeur a changé entre-temps | relire le champ et re-décider |
| `Ce champ n'a pas été extrait` | OCR n'a rien lu | saisir la valeur lue sur le document |
| `Aperçu non disponible pour ce type de fichier` | format non prévisualisable | ouvrir le fichier d'origine |

## 9. Ce que ce logiciel ne fait pas

Pour éviter toute mauvaise interprétation en production :

* il **ne calcule pas** de retenues à la source (`withholding`) — non implémenté ;
* il **ne gère pas** les droits des personnes (accès/rectification, Law 18-07) — non implémenté ;
* il **ne dépose pas** de G50 : il prépare des agrégats, le dépôt reste manuel ;
* il **n'émet pas** de facture client au sens fiscal — il traite les factures fournisseurs ;
* il **ne remplace pas** un `Expert-Comptable`.

## 10. Vocabulaire

| Terme | Sens |
|---|---|
| SCF | Système Comptable Financier (plan de comptes algérien) |
| DZD | Dinar algérien |
| TVA | Taxe sur la valeur ajoutée |
| Droit de timbre | Droit fiscal sur certains paiements **en espèces** |
| HT / TTC | Hors taxes / toutes taxes comprises |
| Extourne | Annulation par une écriture inverse, l'originale restant visible |
| GR/IR | Marchandises reçues, facture non reçue (comptes `380` / `408`) |
