# TODO

- [x] add different openai processing presets. Example: transcript-cleanup -> keynotes + expertizeme-managers
- [x] allow to define custom presets via config
- [x] create data/config.yml, migrate .env to config.yml
- [x] presets can depends on other presets. Example: all presets depends on transcript-cleanup, but keynotes and expertizeme-managers can be generated parallel.
- [ ] fix speaker-name parsing for ExpertizeMe filenames (src/postprocess.extract_interlocutor_names): strip the "N-минутная онлайн-встреча" prefix, support " и " / " х " / " and " separators, drop parentheticals like "(ExpertizeMe)", keep only "Name Surname". Currently Speaker 1 gets the whole filename prefix as its name (e.g. "30-минутная онлайн-встреча Viktoria Tolstikova(ExpertizeMe)") and the second speaker is sometimes left as "Speaker 2".
- [ ] определять тему разговора одним предложением (detect the conversation topic as a single sentence)
- [ ] определять теги из списка в конфиге (detect tags for a conversation from a configurable list of allowed tags — `tags.allowed` in data/config.yml; draft list already seeded there)
- [ ] у каждого сотрудника есть папка, нужно переделать folder_ids в folders, в списке будут объекты {folder_id, name, email}
- [ ] добавить webhooks на завершение обработки файла, на него будут отправляться данные сотрудника и результаты анализов
