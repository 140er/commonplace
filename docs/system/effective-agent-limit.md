### One effective agent limit (card 671)

- Admin `limits` projects, the instance admin space inventory, the project view and every claim or start read `orchestration.resolve`: the smaller of the project limit and the accepted config's limit (the config binds only with `[runner]`), else the default of 3 with the workspace switch on, else no limit.
- Each Admin project row adds `accepted_config_version`, `accepted_config_applies` and `limit_source` (`accepted_config`, `project`, `default`, `unlimited`). `accepted_config_max_agents` shows the config's number even when it is not applied. The instance admin inventory adds `accepted_config_applies` and reads `cap_source` from the same record.
- The project view adds `effective_limit` with `configured`, `config`, `config_version`, `config_applies`, `effective` and `source`.
- Root cause: Admin read the accepted config only with the workspace switch on, and the claim check with the switch off read only the project limit, so an accepted config's limit was shown as unlimited and a second agent could claim a card beyond it, while starts already honoured it. Both now use the resolver; no limit is loosened.
