# Guide utilisateur — ERP algérien (FR)

Ce guide décrit le circuit réel d'une facture fournisseur, tel que le logiciel
l'impose aujourd'hui. Les écrans du client de bureau sont en français (sens de
lecture de gauche à droite) et en arabe (droite à gauche) ; le comportement décrit
ici est identique dans les deux langues.

---

## 1. Rôles et ce qu'ils peuvent faire

| Rôle | Peut faire | Ne peut pas |
|---|---|---|
| `clerk` (saisie) | importer un document, corriger un champ, soumettre une facture | approuver, comptabiliser, lire les états financiers |
| `warehouse` (magasin) | consulter le stock et les bons de réception | lire les factures et les états financiers |
| `approver` | approuver ou refuser une facture soumise | comptabiliser |
| `accountant` | comptabiliser, consulter la balance et le G50 | approuver sa propre soumission |
| `treasury` | consulter les échéances et les paiements | modifier une facture approuvée |
| `auditor` | consulter et vérifier la piste d'audit | modifier quoi que ce soit |
| `admin` | gérer les utilisateurs et l'entreprise | — (ses actions sont auditées) |

La séparation est volontaire : celui qui saisit n'approuve pas, celui qui approuve
ne comptabilise pas.

## 2. Se connecter

1. Ouvrir l'application, saisir identifiant et mot de passe.
2. Le jeton de session est valable 8 heures par défaut et peut être révoqué
   immédiatement (`Se déconnecter`).
3. Après **10 tentatives échouées** pour le même compte depuis le même poste, les
   tentatives sont refusées pendant **5 minutes**. Une connexion réussie remet le
   compteur à zéro ; attendre la fin de la fenêtre suffit, il n'y a pas de
   blocage définitif.

## 3. Importer une facture fournisseur

1. **Documents → Importer**, choisir un PDF.
2. Le système enregistre le fichier chiffré, lit le document (OCR) et extrait les
   champs. **Rien n'est comptabilisé à ce stade.**
3. Deux indicateurs accompagnent chaque champ :
   * la **valeur extraite** ;
   * la **confiance**, avec sa provenance (`mesurée`, `non mesurée`). Une confiance
     « non mesurée » signifie que le moteur ne fournit pas de score pour ce champ :
     elle n'est jamais inventée.
4. Si le texte est illisible, le document est orienté vers la **saisie manuelle**
   et le motif est enregistré.

## 4. Réviser les champs (l'étape qui compte)

Chaque champ doit recevoir une décision. Trois décisions sont possibles :

| Décision | Effet |
|---|---|
| **Confirmer** | vous attestez que la valeur affichée est correcte |
| **Corriger** | vous saisissez la bonne valeur ; l'original reste conservé |
| **Signaler** | vous signalez un doute — **cela ne valide pas le champ** |

Le bouton **Soumettre** reste bloqué tant qu'un champ obligatoire n'a pas été
confirmé ou corrigé. Le motif du blocage est affiché (par exemple
`FIELD_NOT_REVIEWED:amount_ht_dzd`).

### 4.1 Deux valeurs pour le même champ (doublons)

Lorsqu'un même champ apparaît deux fois dans le document — deux pages, deux
lectures — **le système ne choisit jamais à votre place** et n'efface rien.

* Le blocage affiche `DUPLICATE_EXTRACTION_ROWS:<champ>` et liste les lignes
  concernées.
* Vous sélectionnez explicitement **la ligne qui fait foi** et confirmez.
* La décision enregistre les lignes examinées : si une troisième ligne apparaît
  ensuite, le champ se rouvre — ce qui est voulu.
* **Signaler** ne suffit pas à lever un doublon.
* Si les deux valeurs sont différentes et qu'aucune n'est juste, **corrigez** avec
  la bonne valeur : les deux lignes d'origine restent stockées comme preuve.

## 5. Soumettre, approuver, comptabiliser

1. **Soumettre** (`clerk`) : contrôle que tous les champs sont révisés, que les
   totaux sont cohérents (HT, TVA, timbre, TTC) et que le rapprochement
   commande/réception/facture est respecté.
2. **Approuver** (`approver`) : l'approbation est liée au contenu exact de la
   facture. Toute modification financière ultérieure l'annule automatiquement et
   renvoie la facture en révision — il faut donc soumettre à nouveau.
3. **Comptabiliser** (`accountant`) : écritures générées, facture figée.
   Le contrôle de révision est **revérifié à ce moment** : si une preuve a changé
   entre-temps, la comptabilisation est refusée et la facture revient en révision
   avec le motif enregistré. Rien n'est comptabilisé.

Une facture comptabilisée **ne se modifie pas**. La correction se fait par **note
de crédit** (contre-passation).

## 6. Taxes

* La TVA appliquée doit figurer dans la table de taux configurée pour l'entreprise
  **à la date de la facture** ; sinon la facture reste en révision.
* Le droit de timbre ne s'applique qu'aux paiements **en espèces**, au-delà du
  seuil configuré.
* Si aucune règle n'est applicable à la date de la facture, la facture est
  **refusée** au lieu d'utiliser un taux par défaut : c'est volontaire.

> Les taux, seuils et tranches proviennent de la configuration et doivent être
> validés par un expert-comptable. Le logiciel ne certifie aucun taux.

## 7. Déclaration G50

`Comptabilité → G50` produit un classeur préparatoire pour un mois donné, à partir
des écritures **comptabilisées** uniquement. Chaque montant est traçable jusqu'à
l'écriture qui le produit. La mention affichée est explicite : ce classeur est
préparatoire et **exige la validation d'un expert-comptable** avant dépôt.

## 8. Que faire en cas de problème

| Symptôme | Cause probable | Action |
|---|---|---|
| « Trop de tentatives » (429) | trop de mots de passe erronés depuis ce poste | attendre la fin de la fenêtre (5 min), puis réessayer |
| Soumission bloquée | champ non révisé, doublon non résolu, ou total incohérent | lire le motif affiché ; il nomme le champ ou la ligne |
| « Taux de TVA invalide » | taux absent de la configuration à cette date | vérifier la table de taux avec le comptable |
| Comptabilisation refusée après approbation | une preuve a changé depuis l'approbation | réviser les champs signalés, soumettre et approuver de nouveau |
| Facture en révision après comptabilisation refusée | comportement normal | aucune donnée n'a été comptabilisée ; corriger puis reprendre |
