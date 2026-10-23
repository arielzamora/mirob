"""Stopwords en español para el TF-IDF.

Las "stopwords" son palabras tan comunes ("el", "de", "qué", "cuál") que no ayudan a distinguir
un chunk de otro. Sin filtrarlas, una pregunta fuera de tema como "¿Cuál es la capital de
Mongolia?" coincidía con cualquier chunk que tuviera "es", "la" o "de", y alcanzaba un score
de ~0,22 que pasaba el umbral y gastaba tokens del LLM. Filtradas, esa pregunta no comparte
ninguna palabra con el contenido y da 0,000 (ver docs/adr/0001-retrieval-lexico-tfidf.md).

Están escritas SIN acentos: el vectorizer corre con strip_accents="unicode", que convierte
"cómo" en "como" antes de buscar la palabra en esta lista.

scikit-learn solo trae stopwords en inglés (por eso la lista es propia).
"""

STOPWORDS_ES: tuple[str, ...] = (
    # Artículos, preposiciones y conjunciones
    "a", "al", "ante", "con", "contra", "de", "del", "desde", "e", "el", "en", "entre", "la", "las",
    "lo", "los", "ni", "o", "para", "pero", "por", "porque", "si", "sin", "sobre", "u", "un", "una",
    "uno", "unos", "unas", "y",
    # Pronombres y posesivos
    "algo", "algunas", "algunos", "ella", "ellas", "ellos", "esa", "esas", "ese", "eso", "esos",
    "esta", "estas", "este", "esto", "estos", "le", "les", "me", "mi", "mis", "nada", "nos", "os",
    "otra", "otras", "otro", "otros", "se", "su", "sus", "te", "ti", "tu", "tus", "yo",
    # Interrogativos y relativos
    "como", "cual", "cuales", "cuando", "cuanta", "cuantas", "cuanto", "cuantos", "donde", "que",
    "quien", "quienes",
    # Cuantificadores y adverbios muy comunes
    "mas", "mucho", "muy", "no", "poco", "tambien", "tan", "todo", "todos", "ya",
    # Verbos auxiliares y muy genéricos ("¿tienen?", "¿hacen?", "¿puedo?")
    "era", "eran", "es", "estan", "estar", "fue", "ha", "han", "hace", "hacen", "hay", "puedo",
    "pueden", "pueda", "sea", "ser", "son", "tiene", "tienen",
)
