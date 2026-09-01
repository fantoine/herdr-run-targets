"""Plugin Herdr run-targets."""

# Protocole entre les deux points d'entrée : `toggle` pose cette variable sur
# l'onglet qu'il crée, `dashboard` ne se renomme que si elle est présente.
# Définie ici pour qu'une seule chaîne existe des deux côtés de la frontière.
TAB_OWNED_ENV = "RUN_TARGETS_TAB_OWNED"
TAB_LABEL = "run"
