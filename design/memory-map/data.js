/* Sample memory for the map. Plain names in the UI; the store underneath is mempalace:
   theme = wing, topic = room, memory = drawer (verbatim text), person = knowledge-graph entity,
   fact = knowledge-graph triple with valid_from / valid_to, card = board card linked to a drawer. */

const THEMES = [
  { id: "commonplace", color: "#a9bdf5" },
  { id: "computercraft", color: "#7fd18b" },
  { id: "home", color: "#e5c15d" },
  { id: "family", color: "#f0a3a3" },
  { id: "running", color: "#6fcfc4" },
  { id: "reading", color: "#c6a6f2" },
  { id: "money", color: "#d9dbd6" },
];

const PEOPLE = [
  { id: "mila", name: "Mila" },
  { id: "oma", name: "Oma Rie" },
  { id: "sam", name: "Sam" },
  { id: "noor", name: "Noor" },
  { id: "tim", name: "Tim" },
  { id: "ayaan", name: "Ayaan" },
  { id: "visser", name: "Dr. Visser" },
  { id: "lotte", name: "Lotte" },
];

// Knowledge-graph triples. A fact with `to` has ended and stays in the graph as history.
const FACTS = [
  ["mila", "sister of", "jeffrey"],
  ["mila", "lives in", "Utrecht", "2026-03"],
  ["mila", "lives in", "Zwolle", "2019-08", "2026-03"],
  ["mila", "trains with", "sam", "2026-09"],
  ["oma", "grandmother of", "mila"],
  ["oma", "birthday", "14 November"],
  ["oma", "lives in", "Zwolle"],
  ["sam", "runs with", "jeffrey", "2025-05"],
  ["noor", "works on", "Commonplace", "2026-08"],
  ["noor", "works on", "kanzo", "2025-01", "2026-07"],
  ["tim", "plays on", "the ComputerCraft server", "2024-11"],
  ["tim", "built with", "ayaan", "2026-10"],
  ["ayaan", "plays on", "the ComputerCraft server", "2026-02"],
  ["visser", "GP of", "jeffrey", "2023-04"],
  ["lotte", "neighbour of", "jeffrey", "2024-09"],
  ["lotte", "keeps", "the spare key", "2026-07"],
];

const CARDS = [
  { id: 145, board: "commonplace", state: "review", title: "Pick the token expiry default" },
  { id: 142, board: "commonplace", state: "doing", title: "Fix the Windows lock on repair" },
  { id: 150, board: "commonplace", state: "review", title: "Phone push for the Decide count" },
  { id: 154, board: "commonplace", state: "todo", title: "⌘K: tasks and memories in one result list" },
  { id: 153, board: "home", state: "backlog", title: "Add Grok calendar sync" },
  { id: 160, board: "computercraft", state: "todo", title: "Turtle refuel route" },
  { id: 161, board: "home", state: "todo", title: "Book the boiler service" },
];

// [theme, topic, date, source, verbatim text, people, cards]
const WRITTEN = [
  ["commonplace", "tokens", "2026-10-28 14:02", "claude-code:vm", "Personal tokens default to 90 days. A year felt safe until I pictured one sitting in a leaked dotfile.", [], [145]],
  ["commonplace", "tokens", "2026-10-21 09:40", "claude-code:vm", "Prefix every token with cmmn_ so secret scanners can spot them.", [], [145]],
  ["commonplace", "boards", "2026-10-26 22:15", "phone", "The Decide count is the only number I want on my phone. Everything else can wait for the laptop.", [], [150]],
  ["commonplace", "boards", "2026-10-19 11:03", "codex:vm", "Claims expire after 30 minutes without a write. Long enough for a build, short enough that a dead agent frees the card.", [], []],
  ["commonplace", "recall", "2026-10-02 16:30", "claude-code:vm", "Recall has to return my words, not a summary of them. If it paraphrases once, I stop trusting it.", [], []],
  ["commonplace", "boards", "2026-10-12 10:12", "claude-code:vm", "Noor asked whether boards should follow the memory themes. Yes: one theme, one board scope.", ["noor"], []],
  ["commonplace", "site", "2026-10-24 20:48", "claude-code:vm", "The site should look like a terminal that got a design budget. Orange blocks, square corners, no gradients.", [], []],
  ["commonplace", "repair", "2026-10-29 08:55", "codex:vm", "Windows keeps the lock on the store after repair. Retry with backoff, then tell me which process holds it.", [], [142]],
  ["commonplace", "recall", "2026-10-27 13:21", "claude-code:vm", "⌘K should search tasks and memories in one list, ranked together.", [], [154]],
  ["commonplace", "recall", "2026-09-30 19:07", "claude-code:vm", "Hooks get 500 ms. If saving takes longer it goes to the background and the chat never waits.", [], []],
  ["computercraft", "turtles", "2026-10-25 23:12", "discord", "Turtle 7 lost its chunk loader again at y=-52. Moving the quarry into the spawn chunks.", ["tim"], []],
  ["computercraft", "server", "2026-10-18 21:30", "discord", "Tim wants the item network on Create belts instead of wired modems. Belts are slower but you can see them.", ["tim"], []],
  ["computercraft", "server", "2026-10-09 18:44", "claude-code:vm", "16 MB per computer is plenty. The base OS was the problem, not the scripts.", [], []],
  ["computercraft", "reactor", "2026-10-14 22:05", "claude-code:vm", "Reactor control: SCRAM above 1200 K, then page me in the in-game chat.", [], []],
  ["computercraft", "turtles", "2026-10-27 19:50", "discord", "Refuel route: lava from the nether pool, one bucket per thousand blocks.", [], [160]],
  ["computercraft", "server", "2026-10-03 17:20", "claude-code:vm", "Never test on the live server. Copy the world and run it on the VM.", [], []],
  ["computercraft", "server", "2026-10-29 00:41", "discord", "Tim and Ayaan built the train station in one night. The schedule script is still mine.", ["tim", "ayaan"], []],
  ["home", "boiler", "2026-10-22 07:58", "phone", "Boiler pressure dropped to 0.8 bar again. Topped it up to 1.5; book the service before November.", [], [161]],
  ["home", "flat", "2026-10-11 12:30", "phone", "Lotte says the landlord will pay for new window seals if I send photos first.", ["lotte"], []],
  ["home", "garden", "2026-09-21 15:10", "phone", "Tomatoes did nothing on the north balcony. Next year: herbs here, tomatoes at Oma's.", ["oma"], []],
  ["home", "calendar", "2026-10-16 09:02", "grok:cos", "Calendar sync would put bin day on the board. Paper bins every other Thursday.", [], [153]],
  ["home", "flat", "2026-07-30 18:26", "phone", "Spare key is with Lotte now, not under the mat.", ["lotte"], []],
  ["family", "mila", "2026-03-14 20:00", "phone", "Mila moved to Utrecht. Her new place is ten minutes from the station.", ["mila"], []],
  ["family", "oma", "2026-10-20 13:45", "phone", "Oma's birthday is 14 November. She wants photos printed, not a frame.", ["oma"], []],
  ["family", "oma", "2026-08-02 14:15", "phone", "Call Oma on Sundays after lunch, not before. She naps at one.", ["oma"], []],
  ["family", "mila", "2026-09-07 10:30", "phone", "Mila is training for the Utrecht half too. We should do the long runs together.", ["mila", "sam"], []],
  ["family", "recipes", "2026-01-11 17:40", "notes", "Dad's erwtensoep: soak overnight, rookworst in for the last 20 minutes only.", [], []],
  ["running", "training", "2026-10-07 07:15", "phone", "Sam's rule: the long run is slow enough to talk the whole way.", ["sam"], []],
  ["running", "knee", "2026-10-13 18:20", "phone", "Left knee hurt after 18 km on the dike. Dr. Visser says it's the IT band, not the joint.", ["visser"], []],
  ["running", "race", "2026-09-29 21:00", "phone", "Half marathon on 22 March 2027. Goal: under 1:50.", [], []],
  ["running", "gear", "2026-10-23 19:35", "phone", "New shoes at 640 km on the old pair. Same model, half a size up.", [], []],
  ["running", "training", "2026-10-28 06:50", "phone", "Intervals on Tuesdays with Sam: 6 × 800 m, 90 s rest.", ["sam"], []],
  ["reading", "notes", "2026-08-18 23:10", "notes", "Luhmann's slip box worked because every card pointed at another card. That's the whole trick.", [], []],
  ["reading", "books", "2026-09-12 22:40", "notes", "Finished The Dispossessed. Anarres felt less like a utopia than a place with honest bookkeeping.", [], []],
  ["reading", "notes", "2026-08-19 08:05", "notes", "Method of loci: put the thing somewhere you'll walk past again.", [], []],
  ["reading", "books", "2026-10-15 12:12", "claude-code:vm", "Noor recommended How Buildings Learn. The part about low-road buildings fits software too.", ["noor"], []],
  ["reading", "books", "2026-10-29 16:00", "phone", "Library hold for Piranesi is ready until Friday.", [], []],
  ["money", "rent", "2026-10-17 11:30", "phone", "Rent goes up to €1,145 from January. Lotte got the same letter.", ["lotte"], []],
  ["money", "savings", "2026-09-03 20:20", "phone", "Move the buffer to savings at 2.6%. Keep three months of rent in checking.", [], []],
  ["money", "costs", "2026-10-05 10:45", "claude-code:vm", "The VM costs €18 a month. Worth it while the agents run on it.", [], []],
  ["money", "costs", "2026-10-21 21:15", "phone", "Split Oma's birthday dinner with Mila: €62 each.", ["mila", "oma"], []],
];

// Filler so the map has the density of a year of use. {a|b} picks one; # is a number.
const FILL = {
  commonplace: {
    topics: ["tokens", "boards", "recall", "site", "repair", "hooks"],
    people: ["noor"],
    lines: [
      "Test run on {local-1|win|pc-2}: # passed, {0|1|2} failed in {searcher|miner|boards}.",
      "Keep {the hook line|the Decide queue|the stamp line} to one line. If it wraps, it's too much.",
      "{Noor|I} found the {BM25|closet} boost was ranking stale notes first. Fixed by date decay.",
      "Agents can file into backlog only. Moving a card out of backlog is mine.",
      "Search shows the original text first, then where it came from. Never the other way round.",
      "{Split|Merge} the {miner|recall|board} tests; the {slow|flaky} one moves to the nightly run.",
      "Ask the agent for {one question|two options}, not an essay. The Decide row has room for that.",
    ],
  },
  computercraft: {
    topics: ["turtles", "server", "reactor", "trains"],
    people: ["tim", "ayaan"],
    lines: [
      "Turtle # stuck at y={-40|-52|12|64}. {Chunk unloaded|Out of fuel|Bedrock}.",
      "{Tim|Ayaan} logged off with the {farm|smeltery|station} still running.",
      "Monitor wall needs {4|6|8} more advanced monitors for the train board.",
      "Pocket computer pings the base every {30|60} s. Range drops past the river.",
      "Item counts on the storage screen are off by one stack again.",
      "Rednet channel # is the reactor; never reuse it.",
    ],
  },
  home: {
    topics: ["boiler", "flat", "garden", "calendar", "groceries"],
    people: ["lotte"],
    lines: [
      "Out of {oat milk|coffee|rice|dish soap}. Add it before Saturday.",
      "{Paper|Plastic|Green} bins {Thursday|Tuesday}.",
      "Basil on the kitchen sill is doing better than anything outside.",
      "Radiator in the bedroom needs bleeding again.",
      "Lotte's parcel is in my hallway.",
      "Washing machine filter: clean it every {two|three} months.",
    ],
  },
  family: {
    topics: ["mila", "oma", "recipes", "visits"],
    people: ["mila", "oma"],
    lines: [
      "{Mila|Oma} called about {Sunday|the birthday|Christmas}.",
      "Train to Zwolle at {10:14|11:14|13:14}; Oma picks me up.",
      "Oma's photo album from {1971|1984|1990} needs scanning.",
      "Mila wants the bike back before {winter|the move|March}.",
      "Oma's stamppot: more mustard than you think.",
    ],
  },
  running: {
    topics: ["training", "knee", "race", "gear"],
    people: ["sam", "visser"],
    lines: [
      "# km easy, {5:40|5:55|6:10}/km. Knee fine.",
      "Long run {16|18|21} km with Sam along the {dike|river|canal}.",
      "Skipped the run: {rain|knee|work}. Doing it tomorrow.",
      "Physio exercises: {clamshells|step-downs|foam roller}, 3 × 15.",
      "Tuesday intervals: {5|6|8} × {400|800} m.",
    ],
  },
  reading: {
    topics: ["books", "notes", "articles"],
    people: ["noor"],
    lines: [
      "Underlined in {Piranesi|The Dispossessed|How Buildings Learn}: page #.",
      "Notes are only useful if you can find them from {somewhere else|a person|a date}.",
      "{Noor|Mila} lent me {a Le Guin|a Calvino|a Jemisin}.",
      "Saved an article on {zettelkasten|spaced repetition|local-first software}.",
    ],
  },
  money: {
    topics: ["rent", "savings", "costs"],
    people: ["lotte", "mila"],
    lines: [
      "Paid {€24|€31|€48|€72} for {groceries|the energy bill|the phone|the VM}.",
      "{Netflix|Spotify|The gym} went up {€1|€2|€3}. Keep it {for now|until March}.",
      "Transfer € # to savings on the {1st|25th}.",
      "Tax return {sent|due} {in May|next week}.",
    ],
  },
};
