import { escape as e } from './api.js';
import { OWNER_ONLY_COPY, ownerOnlyNote } from './owner-only.js';

// The creator's trust, in the words the person sees next to "created by".
export function trustLine(creator) {
  if (!creator) return 'Creator not recorded';
  const trust = creator.trust ? `${creator.trust} trust` : 'trust unknown';
  return `${creator.name || 'Unknown creator'} · ${trust}${creator.inbox_only ? ' · writes to the inbox only' : ''}`;
}

// A card waiting for release: who made it, how far they are trusted, and the one way to release it.
export function releaseBlock(card) {
  if (!card.awaiting_release) return '';
  // Someone who cannot release it is told why the form is missing; nothing about the creator is shown.
  if (!card.can_approve) return `<section class="callout info" data-release-owner-only><h3>Waiting for release</h3>${ownerOnlyNote(OWNER_ONLY_COPY.release)}</section>`;
  return `<form class="callout info" data-form="release-origin" data-card="${e(card.number)}"><h3>Waiting for release</h3><p data-release-creator>Created by ${e(trustLine(card.creator))}</p><p>Nobody can start this card until it is released.</p><label>Release reason<input name="reason" required maxlength="500"></label><div class="acts"><button class="blockbtn" data-release-origin>Release</button><button type="button" class="btn" data-action="close-panel" title="Keep this waiting for your decision">Hold</button></div></form>`;
}
