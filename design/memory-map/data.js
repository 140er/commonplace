/* Entirely invented sample data for the mockup. The example roles, projects,
   relationships and notes do not describe real people or installations.
   Array shapes match the memory-map renderer contract. */

const THEMES = [
  {"id": "atlas", "color": "#a9bdf5"},
  {"id": "gallery", "color": "#7fd18b"},
  {"id": "workshop", "color": "#e5c15d"},
  {"id": "archive", "color": "#f0a3a3"},
  {"id": "puzzles", "color": "#6fcfc4"},
  {"id": "library", "color": "#c6a6f2"},
  {"id": "exhibits", "color": "#d9dbd6"},
];

const PEOPLE = [
  {"id": "editor", "name": "Example Editor"},
  {"id": "artist", "name": "Example Artist"},
  {"id": "maker", "name": "Example Maker"},
  {"id": "writer", "name": "Example Writer"},
  {"id": "curator", "name": "Example Curator"},
  {"id": "printer", "name": "Example Printer"},
  {"id": "archivist", "name": "Example Archivist"},
  {"id": "guide", "name": "Example Guide"},
];

const FACTS = [
  ["editor", "works with", "artist"],
  ["editor", "edits", "The Paper Atlas", "2026-03"],
  ["editor", "edited", "The Pocket Atlas", "2025-08", "2026-03"],
  ["artist", "illustrates with", "maker", "2026-09"],
  ["writer", "collaborates with", "editor"],
  ["writer", "drafts", "The Sample Catalogue"],
  ["curator", "curates", "The Imaginary Gallery"],
  ["printer", "prints with", "artist", "2025-05"],
  ["maker", "builds", "Paper Orbit", "2026-08"],
  ["maker", "built", "Cardboard Comet", "2025-01", "2026-07"],
  ["guide", "presents", "The Sample Exhibition", "2024-11"],
  ["guide", "collaborates with", "curator", "2026-10"],
  ["archivist", "indexes with", "writer", "2026-02"],
  ["archivist", "catalogues", "The Sample Collection", "2023-04"],
  ["printer", "works with", "maker", "2024-09"],
  ["curator", "reviews with", "archivist", "2026-07"],
];

const CARDS = [
  {"id": 145, "board": "atlas", "state": "review", "title": "Choose the sample atlas cover"},
  {"id": 142, "board": "gallery", "state": "doing", "title": "Align the sample gallery captions"},
  {"id": 150, "board": "atlas", "state": "review", "title": "Label the foldout diagram"},
  {"id": 154, "board": "archive", "state": "todo", "title": "Link the sample catalogue index"},
  {"id": 153, "board": "puzzles", "state": "backlog", "title": "Draft a sample tile puzzle"},
  {"id": 160, "board": "workshop", "state": "todo", "title": "Sketch a folding paper display"},
  {"id": 161, "board": "exhibits", "state": "todo", "title": "Sort the sample exhibit labels"},
];

const WRITTEN = [
  ["atlas", "covers", "2026-09-01 09:15", "sample:editor", "The sample atlas cover uses a blue circle and a wide title.", ["editor"], [145]],
  ["atlas", "covers", "2026-09-02 10:15", "sample:artist", "The Pocket Atlas draft used a square; the new drawing uses a circle.", ["artist"], [145]],
  ["atlas", "diagrams", "2026-09-03 11:15", "sample:maker", "The foldout diagram needs three clearly numbered panels.", ["maker"], [150]],
  ["atlas", "index", "2026-09-04 12:15", "sample:editor", "Put the sample diagram titles in the alphabetical index.", ["editor"], []],
  ["atlas", "captions", "2026-09-05 13:15", "sample:writer", "Each invented illustration gets a short caption and a page reference.", ["writer"], []],
  ["atlas", "captions", "2026-09-06 14:15", "sample:editor", "The example editor and artist are comparing two caption drafts.", ["editor", "artist"], []],
  ["atlas", "diagrams", "2026-09-07 15:15", "sample:artist", "Paper Orbit is an invented drawing of intersecting circles.", ["artist"], []],
  ["atlas", "covers", "2026-09-08 16:15", "sample:printer", "The sample catalogue includes a monochrome version of the cover.", ["printer"], []],
  ["atlas", "index", "2026-09-09 09:15", "sample:archivist", "Link the sample atlas index to the fictional exhibition catalogue.", ["archivist"], [154]],
  ["atlas", "diagrams", "2026-09-10 10:15", "sample:maker", "The reverse side of the foldout holds an invented constellation key.", ["maker"], []],
  ["gallery", "posters", "2026-09-11 11:15", "sample:curator", "The imaginary gallery has a set of seven abstract posters.", ["curator"], []],
  ["gallery", "captions", "2026-09-12 12:15", "sample:artist", "Align the sample captions along the lower edge of each poster.", ["artist"], [142]],
  ["gallery", "shapes", "2026-09-13 13:15", "sample:artist", "The Circle Studies series alternates blue and green paper shapes.", ["artist"], []],
  ["gallery", "posters", "2026-09-14 14:15", "sample:editor", "The fictional poster catalogue uses the same order as the display.", ["editor"], []],
  ["gallery", "layouts", "2026-09-15 15:15", "sample:curator", "Try a two-column arrangement for the sample poster wall.", ["curator"], []],
  ["gallery", "layouts", "2026-09-16 16:15", "sample:artist", "The example artist moved the largest circle drawing to the centre.", ["artist"], []],
  ["gallery", "captions", "2026-09-17 09:15", "sample:guide", "The sample guide will read the illustration titles during the demo.", ["guide"], []],
  ["workshop", "paper", "2026-09-18 10:15", "sample:maker", "Fold the sample paper display into three equal sections.", ["maker"], [160]],
  ["workshop", "models", "2026-09-19 11:15", "sample:coordinator", "The Cardboard Comet is an invented model with removable wings.", ["maker"], []],
  ["workshop", "tools", "2026-09-20 12:15", "sample:printer", "A sample ruler and paper template are listed in the workshop catalogue.", ["printer"], []],
  ["workshop", "models", "2026-09-21 13:15", "sample:maker", "The example maker and printer are comparing two model drawings.", ["maker", "printer"], []],
  ["workshop", "paper", "2026-09-22 14:15", "sample:writer", "The demo folding instructions use dotted lines for hidden edges.", ["writer"], []],
  ["archive", "catalogue", "2026-09-23 15:15", "sample:archivist", "The sample collection contains invented posters, sketches and paper models.", ["archivist"], []],
  ["archive", "index", "2026-09-24 16:15", "sample:archivist", "Index the imaginary collection by title and medium.", ["archivist"], [154]],
  ["archive", "drafts", "2026-09-25 09:15", "sample:writer", "Keep the first sample caption draft alongside the revised version.", ["writer"], []],
  ["archive", "catalogue", "2026-09-26 10:15", "sample:archivist", "The example archivist added a cross-reference from Paper Orbit to Circle Studies.", ["archivist"], []],
  ["archive", "drafts", "2026-09-27 11:15", "sample:editor", "The fictional catalogue entry records a pencil sketch and its final print.", ["editor"], []],
  ["puzzles", "tiles", "2026-09-28 12:15", "sample:maker", "The sample tile puzzle uses five geometric symbols.", ["maker"], [153]],
  ["puzzles", "patterns", "2026-10-01 13:15", "sample:artist", "Alternate circles and triangles in the fictional pattern exercise.", ["artist"], []],
  ["puzzles", "clues", "2026-10-02 14:15", "sample:writer", "The demo puzzle clue refers to a drawing, not to a real location.", ["writer"], []],
  ["puzzles", "tiles", "2026-10-03 15:15", "sample:editor", "The example editor is comparing two invented tile arrangements.", ["editor"], []],
  ["puzzles", "patterns", "2026-10-04 16:15", "sample:maker", "The final sample pattern repeats after six shapes.", ["maker"], []],
  ["library", "catalogues", "2026-10-05 09:15", "sample:archivist", "The Imaginary Library is a fictional collection of art catalogues.", ["archivist"], []],
  ["library", "notes", "2026-10-06 10:15", "sample:writer", "The sample reading card links a drawing technique to an illustration.", ["writer"], []],
  ["library", "catalogues", "2026-10-07 11:15", "sample:editor", "The Paper Atlas and The Sample Catalogue share an invented subject index.", ["editor"], []],
  ["library", "notes", "2026-10-08 12:15", "sample:curator", "The example curator wrote a short note about circular compositions.", ["curator"], []],
  ["library", "sketches", "2026-10-09 13:15", "sample:artist", "The fictional sketchbook contains studies of folded paper and ink lines.", ["artist"], []],
  ["exhibits", "labels", "2026-10-10 14:15", "sample:guide", "Sort the sample exhibit labels alphabetically before the demo.", ["guide"], [161]],
  ["exhibits", "models", "2026-10-11 15:15", "sample:curator", "Place the invented Paper Orbit model beside its drawing.", ["curator"], []],
  ["exhibits", "labels", "2026-10-12 16:15", "sample:guide", "The example guide is checking the spelling of fictional exhibit titles.", ["guide"], []],
  ["exhibits", "sequence", "2026-10-13 09:15", "sample:archivist", "The demonstration ends with the sample catalogue and its index.", ["archivist"], []],
];

// Invented filler for a dense map. {a|b} picks a variant; # is a number.
const FILL = {
  "atlas": {
    "topics": [
      "covers",
      "diagrams",
      "index",
      "captions"
    ],
    "people": [
      "editor",
      "artist"
    ],
    "lines": [
      "Sample drawing # uses {circles|triangles|squares} on a plain background.",
      "The invented atlas has {three|five|seven} foldout diagrams.",
      "The example editor added a caption to sample page #.",
      "Compare the {blue|green|orange} versions of the fictional cover.",
      "The demo index points from the illustration to its paper model."
    ]
  },
  "gallery": {
    "topics": [
      "posters",
      "captions",
      "shapes",
      "layouts"
    ],
    "people": [
      "artist",
      "curator"
    ],
    "lines": [
      "Sample poster # combines {ink lines|paper shapes|pencil marks}.",
      "The imaginary gallery groups the drawings by {shape|medium|title}.",
      "The example artist placed {two|four|six} studies in this series.",
      "Use the fictional catalogue title beneath this sample print.",
      "The demo layout leaves a wide margin around each poster."
    ]
  },
  "workshop": {
    "topics": [
      "paper",
      "models",
      "tools"
    ],
    "people": [
      "maker",
      "printer"
    ],
    "lines": [
      "Sample model # folds along the {dotted|dashed|solid} line.",
      "The invented paper sculpture has {three|four|six} panels.",
      "The example printer is comparing two sample paper textures.",
      "The demo drawing shows the model before and after folding.",
      "Label the sample template with its fictional catalogue title."
    ]
  },
  "archive": {
    "topics": [
      "catalogue",
      "index",
      "drafts"
    ],
    "people": [
      "archivist",
      "writer"
    ],
    "lines": [
      "Sample catalogue entry # links a sketch to its final print.",
      "The fictional archive retains both {pencil|ink|paper} drafts.",
      "The example archivist grouped the samples by {title|medium|series}.",
      "An invented cross-reference joins the atlas and the gallery catalogue.",
      "The demo collection uses a short title for each sample drawing."
    ]
  },
  "puzzles": {
    "topics": [
      "tiles",
      "patterns",
      "clues"
    ],
    "people": [
      "maker",
      "writer"
    ],
    "lines": [
      "Sample puzzle # repeats {circles|triangles|squares} in a row.",
      "The fictional clue points to a shape in the illustration.",
      "The example writer added {two|three|four} invented pattern clues.",
      "The demo tile arrangement is symmetric around its centre.",
      "Compare the sample pattern with its reflected version."
    ]
  },
  "library": {
    "topics": [
      "catalogues",
      "notes",
      "sketches"
    ],
    "people": [
      "editor",
      "archivist"
    ],
    "lines": [
      "Sample note # refers to the fictional {Paper Atlas|Circle Studies|Sample Catalogue}.",
      "The imaginary library links each art catalogue to a sample index.",
      "The example editor annotated a drawing of {folded paper|ink lines|geometric shapes}.",
      "A fictional sketchbook entry shows three versions of the same shape.",
      "The demo reading card includes a title and a page reference."
    ]
  },
  "exhibits": {
    "topics": [
      "labels",
      "models",
      "sequence"
    ],
    "people": [
      "guide",
      "curator"
    ],
    "lines": [
      "Sample exhibit # has a {blue|green|orange} label.",
      "The fictional exhibition groups {drawings|models|posters} in a short sequence.",
      "The example guide introduces the invented catalogue before the model.",
      "The demo label repeats the title printed beneath the illustration.",
      "The sample exhibition ends with a view of the catalogue index."
    ]
  }
};
