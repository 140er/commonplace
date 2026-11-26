- **Owns:**

  - The owner's Needs me group `setup_proposals` (`BoardService._setup_proposals`, card #675): present for a signed-in owner only, counted in `count`, absent for agent tokens (members cannot read the owner queue). Items come from `commonplace/projects/setups.py` `pending`; an unreadable store gives an empty list and is logged, never a failed queue.
