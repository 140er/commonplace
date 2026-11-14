// Runs before first paint (a plain blocking script: the page allows no inline script), so the
// Deck look never flashes classic. Same rule as deckOn/applyDeck in deck.js: ?ui=deck|classic
// wins for this visit, else only a stored 'classic' opts out; unreadable storage means Deck.
(function () {
  var root = document.documentElement, on = true, asked = null, saved = null;
  try { asked = new URLSearchParams(location.search).get('ui'); } catch (error) { asked = null; }
  try { saved = localStorage.getItem('cmmn.ui'); } catch (error) { saved = null; }
  if (asked === 'deck' || asked === 'classic') on = asked === 'deck';
  else on = saved !== 'classic';
  if (on) root.dataset.ui = 'deck'; else delete root.dataset.ui;
})();
