# FileAnalyser

Outil d'analyse de disque dur avec visualisation sunburst interactive et scan à la demande.

![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20Mac-orange.svg)

---

## Fonctionnalités

- **Scan itératif** : Pas de limite de profondeur (stack instead of recursion)
- **Visualisation Sunburst** : Graphique interactif D3.js
- **Scan à la demande** : Clic sur un dossier → scan complet automatique
- **Exclusions système** : Auto-exclude Windows system folders
- **Progress bar CLI** : Affichage en temps réel (fichiers + taille)
- **Cross-platform** : Windows, Linux, Mac
- **API HTTP** : Serveur léger pour interface web

---

## Installation

```bash
# Cloner ou télécharger le projet
cd FileAnalyser

# Aucune dépendance externe (Python standard library uniquement)
python --version  # Python 3.8+
```

---

## Démarrage rapide

### 1. Lancer le serveur web

```bash
python app.py --port 8000
```

### 2. Ouvrir dans le navigateur

```
http://localhost:8000/sunburst.html
```

### 3. Scanner un disque

- Entrez un chemin (ex: `C:\`) ou cliquez sur un bouton de lecteur
- Cliquez sur **Scan**
- Cliquez sur un dossier → **"Scan this folder"** pour scanner en profondeur

---

## Interface Web

| Bouton | Action |
|--------|--------|
| C:\ D:\ etc. | Scan rapide du lecteur |
| Scan | Lance le scan initial (depth 5) |
| Clic segment | Zoom sur le dossier |
| Scan this folder | Scan complet à la demande
| Reset | Revenir à la racine
| Legend | Types de fichiers dominants

---

## CLI Scanner

### Commandes

```bash
# Scan basique
python scanner.py C:\ -o output/scan.json

# Avec visualisation HTML (fichier unique)
python scanner.py C:\ --html -o output/scan.json

# Scan rapide (3 niveaux):

```bash
python scanner.py C:\ -d 3 --html -o output/scan.json

# Taille min fichier (10MB):

```bash
python scanner.py C:\ -m 10485760 --html

# Exclure dossiers spécifiques:

```bash
python scanner.py C:\ -e "node_modules" "temp" ".cache"

# Scan silencieux (sans progress bar):

```bash
python scanner.py C:\ --no-progress -o output/scan.json

# Inclure dossiers système (par défaut exclus):

```bash
python scanner.py C:\ --no-system -o output/scan.json
```

### Options

| Option | Description | Exemple |
|--------|-------------|---------|
| `-o, --output` | Fichier de sortie JSON | `-o scan.json` |
| `-d, --max-depth` | Profondeur max | `-d 3` |
| `-m, --min-size` | Taille min fichier (octets) | `-m 10485760` |
| `-e, --exclude` | Patterns à exclure | `-e "temp" "cache"` |
| `--html` | Générer HTML | `--html` |
| `--no-progress` | Sans progress bar | `--no-progress` |
| `--no-system` | Inclure dossiers système | `--no-system` |

---

## Exclusions système

Par défaut, ces dossiers sont exclus automatiquement :

| Dossier | OS | Raison |
|---------|-----|--------|
| `$RECYCLE.BIN` | Windows | Corbeille |
| `System Volume Information` | Windows | Inaccessible |
| `Windows` | Windows | Fichiers OS |
| `Program Files` | Windows | Apps installées |
| `Program Files (x86)` | Windows | Apps 32-bit |
| `ProgramData` | Windows | Caches système |
| `.git/objects` | All | Très volumineux |
| `node_modules/.cache` | All | Caches |

**Root detection** : Les exclusions sont actives uniquement pour `C:\` (racine Windows).

---

## Serveur HTTP

### Lancement

```bash
python app.py --port 8000 --bind 127.0.0.1
```

### API Endpoints

```
GET /                          → Page HTML
GET /sunburst.html             → Interface
GET /api/drives                → Liste des lecteurs
GET /api/scan?path=C:\&depth=5 → Scan initial
GET /api/scan_dir?path=C:\Games → Scan complet dossier
```

### Exemple curl

```bash
# Liste des lecteurs
curl "http://localhost:8000/api/drives"

# Scan initial
curl "http://localhost:8000/api/scan?path=C:\Users\&depth=5"

# Scan complet d'un dossier
curl "http://localhost:8000/api/scan_dir?path=C:\Users\Documents"
```

---

## Format JSON

```json
{
  "scan_info": {
    "root": "C:\\Users",
    "max_depth": 5,
    "total_size": 5368709120,
    "total_size_formatted": "5.00 GB",
    "total_files": 1234,
    "total_dirs": 56,
    "errors_count": 2,
    "system_exclusions_applied": ["$RECYCLE.BIN", "System Volume Information", "Windows"]
  },
  "errors": [
    { "path": "C:\\$RECYCLE.BIN\\test", "type": "PermissionError", "message": "Access denied" }
  ],
  "tree": {
    "name": "Users",
    "path": "C:\\Users",
    "type": "dir",
    "size": 5368709120,
    "children": [...]
  }
}
```

---

## Structure du projet

```
FileAnalyser/
├── scanner.py       # CLI scan (itératif)
├── app.py           # Serveur HTTP
├── utils.py         # Helpers (format, save)
├── sunburst.html    # Interface web
├── output/          # Fichiers de sortie
└── README.md        # Ce fichier
```

---

## Performance

| Scenario | Temps estimé |
|----------|--------------|
| Scan C:\ (500GB, 100k files) | 30-60s |
| Scan D:\ (1TB, 200k files) | 1-5 min |
| Scan "Documents" (~10GB) | 5-15s |

---

## Licence

MIT
