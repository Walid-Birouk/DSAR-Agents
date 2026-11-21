"""Word lists for the generator: names, places, Ferlane's catalogue, helpdesk staff.

Names are common first and last names combined at random; the contact data attached to them is always
fictional (see fiction.py). Cities and postcodes are real places, which identify no one.
"""

from __future__ import annotations

FIRST_NAMES_FR = (
    "Camille", "Léa", "Manon", "Chloé", "Emma", "Inès", "Sarah", "Julie", "Pauline", "Claire",
    "Lucie", "Margaux", "Élodie", "Aurélie", "Sophie", "Nathalie", "Isabelle", "Céline", "Hélène", "Amandine",
    "Yasmine", "Nadia", "Fatou", "Mélanie", "Anaïs", "Lucas", "Hugo", "Louis", "Thomas", "Nicolas",
    "Julien", "Antoine", "Maxime", "Alexandre", "Romain", "Kevin", "Mathieu", "Sébastien", "Olivier",
    "Pierre",
    "Mehdi", "Karim", "Youssef", "Bastien", "Florian", "Guillaume", "Vincent", "Jérôme", "Arnaud", "Damien",
)  # fmt: skip

LAST_NAMES_FR = (
    "Martin", "Bernard", "Dubois", "Thomas", "Robert", "Richard", "Petit", "Durand", "Leroy", "Moreau",
    "Simon", "Laurent", "Lefebvre", "Michel", "Garcia", "David", "Bertrand", "Roux", "Vincent", "Fournier",
    "Morel", "Girard", "André", "Lefèvre", "Mercier", "Dupont", "Lambert", "Bonnet", "François", "Martinez",
    "Legrand", "Garnier", "Faure", "Rousseau", "Blanc", "Guerin", "Muller", "Henry", "Roussel", "Nicolas",
    "Perrin", "Morin", "Mathieu", "Clement", "Gauthier", "Dumont", "Lopez", "Fontaine", "Chevalier", "Robin",
    "Masson", "Sanchez", "Boyer", "Denis", "Lemaire", "Duval", "Joly", "Gautier", "Roger", "Roche",
    "Benali", "Haddad", "Diallo", "Nguyen", "Traoré", "Carvalho", "Ferreira", "Kaya", "Meunier", "Barbier",
)  # fmt: skip

FIRST_NAMES_EN = (
    "Emily", "Olivia", "Grace", "Hannah", "Charlotte", "Amelia", "Rachel", "Laura", "James", "Oliver",
    "Jack", "Daniel", "Matthew", "Samuel", "Ryan", "Ethan",
)  # fmt: skip

LAST_NAMES_EN = (
    "Smith", "Jones", "Taylor", "Brown", "Wilson", "Evans", "Walker", "Wright", "Hughes", "Edwards",
    "Turner", "Parker", "Collins", "Murphy", "Bennett", "Cooper",
)  # fmt: skip

PLACES = (
    ("Paris", "75011"), ("Paris", "75015"), ("Paris", "75019"), ("Lyon", "69003"), ("Lyon", "69007"),
    ("Marseille", "13006"), ("Toulouse", "31000"), ("Nice", "06000"), ("Nantes", "44000"),
    ("Montpellier", "34000"), ("Strasbourg", "67000"), ("Bordeaux", "33000"), ("Lille", "59000"),
    ("Rennes", "35000"), ("Reims", "51100"), ("Grenoble", "38000"), ("Dijon", "21000"), ("Angers", "49000"),
    ("Tours", "37000"), ("Rouen", "76000"), ("Clermont-Ferrand", "63000"), ("Annecy", "74000"),
    ("Brest", "29200"), ("Limoges", "87000"), ("Pau", "64000"),
)  # fmt: skip

STREETS = (
    "rue des Lilas", "rue Victor Hugo", "avenue Jean Jaurès", "rue de la République", "rue Pasteur",
    "boulevard Gambetta", "rue du Moulin", "impasse des Acacias", "rue des Écoles", "allée des Tilleuls",
    "rue de la Gare", "chemin des Vignes", "rue Voltaire", "place du Marché", "rue des Peupliers",
    "avenue de la Liberté", "rue Jules Ferry", "rue du Château", "rue des Roses", "quai des Bateliers",
)  # fmt: skip

# (sku, French name, English name, price in cents). Ferlane sells home textiles and decor.
PRODUCTS = (
    ("FL-HC-240", "Housse de couette en lin lavé 240x220", "Washed linen duvet cover 240x220", 12900),
    ("FL-HC-140", "Housse de couette en percale 140x200", "Percale duvet cover 140x200", 6900),
    ("FL-DH-160", "Drap housse en coton bio 160x200", "Organic cotton fitted sheet 160x200", 3900),
    ("FL-TO-065", "Taie d'oreiller en lin 65x65", "Linen pillowcase 65x65", 1900),
    ("FL-PL-130", "Plaid en laine mérinos 130x170", "Merino wool throw 130x170", 8900),
    ("FL-CO-045", "Coussin en velours côtelé 45x45", "Corduroy cushion 45x45", 3400),
    ("FL-RI-140", "Rideau en lin occultant 140x260", "Blackout linen curtain 140x260", 7400),
    ("FL-VO-140", "Voilage en gaze de coton 140x240", "Cotton gauze sheer curtain 140x240", 3900),
    ("FL-NA-160", "Nappe en lin enduit 160x250", "Coated linen tablecloth 160x250", 5900),
    ("FL-SE-045", "Lot de 4 serviettes de table", "Set of 4 napkins", 2400),
    ("FL-TB-050", "Serviette de bain en éponge 50x100", "Terry bath towel 50x100", 1900),
    ("FL-DB-100", "Drap de bain gaufré 100x150", "Waffle bath sheet 100x150", 3900),
    ("FL-PE-070", "Peignoir en nid d'abeille", "Honeycomb bathrobe", 6900),
    ("FL-TA-170", "Tapis en jute tressé 120x170", "Braided jute rug 120x170", 9900),
    ("FL-TA-200", "Tapis berbère en laine 160x230", "Berber wool rug 160x230", 24900),
    ("FL-BO-012", "Bougie parfumée figuier", "Fig scented candle", 2900),
    ("FL-VA-030", "Vase en grès émaillé", "Glazed stoneware vase", 3900),
    ("FL-PA-040", "Panier en osier tressé", "Woven wicker basket", 3400),
    ("FL-MI-060", "Miroir rond en rotin 60 cm", "Round rattan mirror 60 cm", 7900),
    ("FL-CA-030", "Cadre photo en chêne 30x40", "Oak photo frame 30x40", 2900),
)  # fmt: skip

STAFF = ("Camille Roussel", "Nadia Benali", "Thomas Girard", "Léa Fontaine", "Hugo Marchand", "Inès Carvalho")

WEB_PATHS = (
    "/", "/linge-de-lit", "/linge-de-bain", "/rideaux", "/tapis", "/decoration", "/promotions",
    "/recherche?q=lin", "/recherche?q=plaid", "/panier", "/livraison", "/aide/retours",
)  # fmt: skip
ACCOUNT_PATHS = (
    "/compte",
    "/compte/commandes",
    "/compte/adresses",
    "/compte/preferences",
    "/commande/paiement",
)

USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/141.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) Safari/18.2",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) Mobile Safari/18.5",
    "Mozilla/5.0 (Linux; Android 15) Chrome/139.0 Mobile",
    "Mozilla/5.0 (X11; Linux x86_64) Chrome/139.0",
)

CONSENT_PURPOSES = ("newsletter", "sms_offers", "partner_offers")
ORDER_STATUSES = ("delivered", "delivered", "delivered", "delivered", "returned", "cancelled")

# Health remarks (special-category data, GDPR Art. 9(1)) that customers volunteer in tickets.
HEALTH_REMARKS_FR = (
    "je suis enceinte de six mois",
    "je me déplace en fauteuil roulant",
    "je suis asthmatique et la poussière me pose problème",
    "je sors d'une chimiothérapie",
    "j'ai une allergie sévère aux acariens",
    "je me remets d'une opération du genou",
)
HEALTH_REMARKS_EN = (
    "I'm six months pregnant",
    "I use a wheelchair",
    "I have asthma and dust is a problem for me",
    "I've just finished chemotherapy",
    "I have a severe dust-mite allergy",
    "I'm recovering from knee surgery",
)

RELATIONS_FR = ("mon mari", "ma femme", "mon compagnon", "ma compagne", "ma voisine", "mon frère")
RELATIONS_EN = ("my husband", "my wife", "my partner", "my neighbour", "my brother", "my sister")
