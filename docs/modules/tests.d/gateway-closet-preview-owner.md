- **Owns:**
  `tests/test_gateway_closet_preview_owner.py`: gateway search keeps closet_preview only for a trusted person owner session that sees all wings; members, API tokens (even owner-role), scoped and untrusted principals lose it; a source mutation that reverts the owner check is caught.
- **Promises:**
  The existing hidden-closet search witness now expects the owner session to receive the preview and every token reader to receive none.
- **How it fails:**
  Dropping the owner exemption fails the owner assertions; widening it to token principals fails the non-owner cases.
- **Tests:**
  `tests/test_gateway_closet_preview_owner.py` and the closet preview case in `tests/test_card253_s11.py`; both run without a browser.
