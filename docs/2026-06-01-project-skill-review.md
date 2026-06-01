# Глубокий обзор проекта и skill-слоя

Дата: 2026-06-01

## Объем анализа

- Проведен read-only аудит репозитория несколькими агентами Explore.
- Локальные опоры проверены вручную: [src/main.py](../src/main.py), [src/config.py](../src/config.py), [src/stt/google_provider.py](../src/stt/google_provider.py), [tests/test_skill_docs.py](../tests/test_skill_docs.py), [.claude/skills/gdstt-cli/SKILL.md](../.claude/skills/gdstt-cli/SKILL.md), [README.md](../README.md), [CLAUDE.md](../CLAUDE.md).
- Отдельно собран внешний benchmark по публичным репозиториям вокруг skills, agent instructions, skill creators и cross-tool packaging.

## Короткий вывод

Этот репозиторий уже сильнее, чем типичный узкий automation-скрипт. Ядро обработки файлов собрано чисто, операторский UX продуман необычно хорошо, а основной skill не просто написан, а частично защищен тестами. Проект уже ближе к небольшому продукту, чем к набору утилит.

Главное ограничение теперь не в базовой бизнес-логике, а в knowledge surface вокруг нее. Сейчас в репозитории есть один сильный operator skill, но еще нет полноценной skill system: отсутствует repo-wide agent surface, нет cross-tool instruction layer, нет формальной skill-governance модели и нет более широкого conformance pipeline для agent-facing артефактов.

Правильный следующий шаг не в том, чтобы немедленно делать красивый skill creator. Правильный следующий шаг в том, чтобы сначала дожать runtime failure semantics, формализовать текущий skill pack, а потом наращивать quality gates и cross-harness support малыми контролируемыми итерациями.

## Анализ проекта

### Что уже сильное

- Поток обработки хорошо разделен между per-item orchestration, one-shot targeting и polling loop в [src/main.py](../src/main.py).
- Загрузка конфигурации централизована в [src/config.py](../src/config.py), а provider validation намеренно сделана так, чтобы Drive-only команды могли работать без полной STT-настройки.
- В кодовой базе выдержаны понятные инварианты: env-driven config, provider dispatch через `src/stt`, тестовая изоляция на моках, отдельный тестовый модуль на каждый source-модуль.
- Архитектурный контекст в [CLAUDE.md](../CLAUDE.md) достаточно качественный, чтобы новый разработчик быстро восстановил общую форму рантайма.
- CLI и skill surface дают оператору рабочую систему действий, а не просто перечисление внутренних функций.

### Основные runtime-риски

- В [src/main.py](../src/main.py) есть локально хрупкая грань вокруг сценария, когда система считает, что MP3 уже существует, но сопутствующая MP3 metadata неполная. Это полезный guard, но он же показывает, что контракт folder-state вокруг sibling-артефактов не полностью самодостаточен.
- Polling loop в [src/main.py](../src/main.py) корректно продолжает работу на большинстве не-auth ошибок, но вокруг transient Drive/API и provider failures пока нет более широкой retry/backoff-стратегии.
- Timeout-handling в [src/stt/google_provider.py](../src/stt/google_provider.py) специально оставляет загруженный blob при timeout long-running operation. Это разумно с точки зрения безопасности данных, но создает manual cleanup path, который должен рассматриваться как явная операционная обязанность.
- Google STT может вернуть пустой transcript result в [src/stt/google_provider.py](../src/stt/google_provider.py). Silent empty output опаснее обычного exception, потому что выглядит как успешная обработка.
- Observability для длительно живущего headless-сервиса пока тонкая: проект умеет логировать и уведомлять, но не дает структурированного ответа на вопросы "что обработалось", "что было пропущено", "что ретраилось" и "где ушли деньги".

### Вывод по runtime

Форма runtime уже достаточно хорошая, чтобы на нее можно было уверенно инвестировать дальше. Следующие инженерные выигрыши лежат в зоне reliability и operational clarity, а не в полном переписывании pipeline.

## Анализ skill-слоя

### Что уже сильное

- Operator skill в [.claude/skills/gdstt-cli/SKILL.md](../.claude/skills/gdstt-cli/SKILL.md) необычно зрелый для репозитория такого размера.
- В нем есть human-facing setup wizard, safety-oriented проверки перед мутациями, guidance по dry-run, size-guardrails и полноценные operator playbooks вместо сухого перечня команд.
- Skill синхронизирован с реальной CLI-поверхностью, а не живет как отдельная устаревающая документация.
- Самый сильный сигнал во всем репозитории дает [tests/test_skill_docs.py](../tests/test_skill_docs.py): проект уже относится к части skill surface как к контракту, который стоит тестировать.

### Чего не хватает до полноценной skill system

- Сейчас есть один сильный вертикальный operator skill, но еще нет широкого skill pack.
- Нет repo-wide AGENTS-style поверхности для cross-tool agent interoperability.
- Нет отдельного developer-oriented skill для добавления провайдеров, разбора runtime failure paths и review архитектурных изменений.
- Troubleshooting knowledge есть фрагментами, но не упаковано в отдельные reference artifacts.
- В репозитории нет явной модели skill governance: нет versioning-подхода, freshness checks, ownership-модели для agent-facing docs и нет formal parity layer между несколькими instruction targets.

### Вывод по skill-слою

Текущий skill уже достаточно хорош, чтобы быть ядром серьезной agent-facing системы. Не хватает не идей, а упаковки, layering и conformance discipline вокруг этого ядра.

## Внешний benchmark

Срез на 2026-06-01.

- ECC, примерно 201k stars. Самый мощный пример большого agent ecosystem с skills, creator workflows, hooks, install surfaces и cross-harness packaging. Главный урок: масштаб приходит из packaging и governance, а не из количества промптов.
- agentsmd/agents.md, примерно 21.8k stars. Сильнейший сигнал в сторону repo-wide open agent instructions. Главный урок: минимальная cross-tool instruction surface должна существовать отдельно от продуктовой документации.
- microsoft/skills, примерно 2.4k stars. Лучший benchmark по industrial skill curation. Главный урок: progressive disclosure плюс acceptance criteria. Основной skill должен быть компактным, а детали должны уходить в references.
- FrancyJGLisboa/agent-skill-creator, примерно 1.3k stars. Самый близкий benchmark именно к skill factory. Главный урок: ценность не в генерации markdown как таковой, а в генерации installable и testable artifacts.
- sno-ai/mda, примерно 563 stars. Самый чистый пример one-source multi-target authoring. Главный урок: долгосрочно сопровождение проще, когда SKILL.md, AGENTS.md и companion instruction files собираются из общей source model.
- agent-sh/agnix, примерно 263 stars. Это missing quality gate layer. Главный урок: linting и conformance checks для agent configs нужны, если instruction surface воспринимается как production infrastructure.

## Стратегический вывод

Проект уже впереди большинства нишевых automation-репозиториев по operator-facing качеству. Он отстает от лучших публичных экосистем не потому, что core pipeline слабый, а потому, что окружающая agent и skill-архитектура пока еще монолитная и repo-local.

Репозиторий должен сначала дойти до состояния "reference-grade vertical skill pack", и только потом имеет смысл думать о "generalized skill creator".

## Что уже тянет на reference-grade

- Разделение runtime на понятные operational слои уже достаточно взрослое: per-item orchestration, on-demand processing, polling loop и provider dispatch не свалены в один procedural комок.
- Конфигурационный слой в [src/config.py](../src/config.py) уже мыслит категориями operator reality, а не только внутренней валидности: Drive-only сценарии не ломаются из-за отсутствующих STT secrets.
- Skill в [.claude/skills/gdstt-cli/SKILL.md](../.claude/skills/gdstt-cli/SKILL.md) заметно сильнее среднего по рынку именно как operator surface: есть setup wizard, guardrails, workflow-first структура и внятный mutation discipline.
- Наличие [tests/test_skill_docs.py](../tests/test_skill_docs.py) переводит skill из уровня "документация для людей" на уровень "частично исполняемый контракт". Это уже reference-grade инстинкт.
- Общая архитектурная документация в [AGENTS.md](../AGENTS.md) уже может быть каноническим shared-слоем, а [CLAUDE.md](../CLAUDE.md) можно держать как тонкий compatibility shim без дублирования сути.

## Что пока не дотягивает до ECC-class

- Нет отдельного repo-wide instruction surface, который можно честно переносить между редакторами и агентными рантаймами. Сейчас знание сосредоточено в repo-local skill и developer note, а не в cross-tool contract.
- Нет полноценной skill-pack архитектуры: отсутствуют отдельные reference-слои для troubleshooting, provider-selection, extension workflow, review discipline и recovery paths.
- Нет формальной governance-модели для agent-facing artifacts: ownership, freshness checks, versioning, release discipline и CI-level parity gates пока выражены лишь частично.
- Нет installable или compilable knowledge model. Лучшие внешние экосистемы выигрывают тем, что их surfaces можно собирать, проверять, lint-ить и переносить; здесь это пока mostly handwritten layer.
- Нет независимой universal-first portability story. Сегодня skill силен в родной среде, но только начинает оформляться вокруг переносимого `AGENTS.md` и одного основного skill вместо набора editor-specific веток.
- Нет evaluation harness для самого knowledge layer. В коде тесты хорошие, но у skill system пока нет полноценного regression pack, который ловил бы semantic drift в разных агентных сценариях.

## Самая жесткая формулировка текущего состояния

Если оценивать проект строго, то код уже на уровне крепкого специализированного сервиса, а knowledge surface пока на уровне сильного одиночного skill, но не на уровне настоящей переносимой agent system.

Иначе говоря: runtime уже ближе к product-grade, а skill ecosystem вокруг него пока ближе к well-crafted local operating manual, чем к полноценной cross-editor knowledge platform.

## Суперподробный roadmap

### Фаза 0: Зафиксировать контракт между документами

Цель: четко определить, какой документ за что отвечает.

Что сделать:

- [README.md](../README.md) оставить как human quickstart и deployment overview.
- [AGENTS.md](../AGENTS.md) закрепить как канонический shared architecture и repo contract.
- [CLAUDE.md](../CLAUDE.md) сделать тонкой совместимой ссылкой на [AGENTS.md](../AGENTS.md), а не отдельным вторым источником истины.
- [.claude/skills/gdstt-cli/SKILL.md](../.claude/skills/gdstt-cli/SKILL.md) оставить как operator execution surface.
- Добавить короткую doc map, чтобы три слоя не перекрывались хаотично.

Критерий приемки:

- Ни один setup flow не описан двумя конфликтующими способами.
- CLI command list, env matrix и ключевые workflow steps согласованы между всеми тремя поверхностями.

### Фаза 1: Runtime hardening P0

Цель: устранить самые рискованные failure modes в текущем pipeline.

Что сделать:

- Добавить явную retry/backoff policy для transient Drive, upload и STT operation failures.
- Ужесточить MP3-state contract вокруг предположений folder-state в [src/main.py](../src/main.py).
- Перевести empty transcript result из неявного успешного состояния в явный degraded или failure outcome.
- Сделать retained-blob timeout path в [src/stt/google_provider.py](../src/stt/google_provider.py) видимым и операционно управляемым.

Критерий приемки:

- Не остается silent-success пути для пустого transcript output.
- Transient infrastructure failures имеют bounded retry policy.
- Оператор может понять, когда после timeout требуется manual blob cleanup.

### Фаза 2: Observability и operator telemetry

Цель: дать оператору точную картину поведения системы на уровне файла и цикла.

Что сделать:

- Добавить structured logging fields: file id, file name, provider, processing mode, duration, retry count, outcome.
- Добавить summary events для каждого run-once цикла и для main loop.
- Явно различать skipped, deferred, failed и successfully processed items.
- Добавить cost-oriented telemetry hooks для платных STT providers.

Критерий приемки:

- По одному запуску можно ответить на вопрос "что произошло" без чтения traceback.
- Cost-relevant операции можно потом аудитить.

### Фаза 3: Превратить текущий skill в настоящий skill pack

Цель: разрезать текущий operator skill на компактный основной skill и специализированные references.

Что сделать:

- Оставить основной skill сфокусированным на activation, command routing и core guardrails.
- Вынести provider matrix, troubleshooting, recovery playbooks и mutation policy в companion docs.
- Добавить явные anti-patterns и failure-handling examples.
- Сохранить сильный operator voice, но убрать лишний context bulk из основного skill-файла.

Критерий приемки:

- Main skill остается достаточно коротким для надежной auto-activation.
- Подробности остаются доступны, но не раздувают primary skill surface.

### Фаза 4: Skill QA и conformance gates

Цель: превратить doc testing в полноценный quality gate для agent artifacts.

Что сделать:

- Расширить [tests/test_skill_docs.py](../tests/test_skill_docs.py) дальше, чем просто command parity.
- Добавить проверки обязательных разделов, provider-specific warnings, safety rails и example validity.
- Добавить drift checks для env documentation и критичных workflow narratives.
- Относиться к skill regression как к обычному CI failure, а не как к ручному замечанию на review.

Критерий приемки:

- Ломающие изменения skill surface валят CI.
- Agent-facing docs не могут тихо сгнить при эволюции кода.

### Фаза 5: Repo-wide agent surfaces

Цель: развести operator knowledge и developer/reviewer knowledge.

Что сделать:

- Добавить минимальную repo-wide AGENTS-style instruction surface для cross-tool compatibility.
- Добавить developer-focused skill или companion doc для provider extension, review flow и debugging failures.
- Добавить reviewer-oriented checklist для архитектурных и operational regressions.

Критерий приемки:

- Разные agent roles работают на правильном уровне абстракции.
- Репозиторий перестает зависеть от одного монолитного operator skill для всех agent-задач.

### Фаза 6: Provider extension kit

Цель: сделать добавление провайдеров повторяемым и низкорисковым.

Что сделать:

- Документировать точный контракт для добавления нового STT provider.
- Зафиксировать обязательные config changes, provider-factory updates, runtime semantics, tests и doc updates.
- Добавить пример полностью корректного пути внедрения нового provider.

Критерий приемки:

- Новый provider можно добавить по документированному checklist, а не через repo archaeology.
- Provider additions имеют предсказуемый test и doc impact.

### Фаза 7: Cross-harness packaging

Цель: поддержать больше одной agent-экосистемы, не обещая универсальность раньше времени.

Что сделать:

- Сохранить один основной skill как operator surface.
- Держать [AGENTS.md](../AGENTS.md) как universal repo surface.
- Избегать editor-specific overlays, пока без них можно обойтись.
- Если инструменту когда-то понадобится свой файл, делать его тонкой ссылкой на `AGENTS.md`, а не отдельной логикой.

Критерий приемки:

- У репозитория появляется честная portability story на небольшой поддерживаемой матрице.
- Ни одна instruction target surface не становится stale duplicate без owner.

### Фаза 8: Governance, freshness и evals

Цель: относиться к skill artifacts как к поддерживаемой продуктовой поверхности.

Что сделать:

- Добавить ownership и update rules для agent-facing docs.
- Добавить changelog или versioning-подход для skill pack.
- Добавить lightweight evaluation scenarios вокруг filename parsing, provider selection, dry-run behavior и transcript handling.
- Добавить периодический freshness review для внешних references и compatibility notes.

Критерий приемки:

- У skill artifacts есть явные maintainers и update triggers.
- Репозиторий может ловить semantic drift в важных operator flows.

### Фаза 9: Creator extraction и one-source multi-target experiments

Цель: только после стабилизации предыдущих слоев начать исследовать generator и compiler workflows.

Что сделать:

- Прототипировать repo-specific skill creator, который умеет собирать operator skill, companion references и cross-tool surfaces.
- Проверить, нужен ли единый source model, из которого собираются SKILL.md, AGENTS.md и companion instruction files.
- Избирательно оценить alignment с MDA-style или agnix-style tooling после стабилизации собственной схемы репозитория.

Критерий приемки:

- Generator output тестируемый, а не просто красиво отформатированный.
- Creator layer реально снижает maintenance cost, а не автоматизирует непоследовательность.

## Расширенный план выполнения по шагам

### Шаг 1: Contract freeze

Сначала формально развести роли [README.md](../README.md), [AGENTS.md](../AGENTS.md), [CLAUDE.md](../CLAUDE.md) и [.claude/skills/gdstt-cli/SKILL.md](../.claude/skills/gdstt-cli/SKILL.md). При этом `AGENTS.md` должен стать shared source of truth, а `CLAUDE.md` — тонкой ссылкой на него.

### Шаг 2: Runtime hardening

Исправить самое уязвимое вокруг [src/main.py](../src/main.py) и [src/stt/google_provider.py](../src/stt/google_provider.py): transient retries, empty transcript policy, cleanup visibility, consistency вокруг sibling-артефактов.

### Шаг 3: Operator observability

Добавить структурированную телеметрию на уровне файла, цикла и стоимости. Оператор должен видеть не только факт ошибки, но и картину всей обработки.

### Шаг 4: Productize skill pack

Разделить текущий skill не на множество равноправных подскилов, а на один основной компактный skill и companion references: provider matrix, troubleshooting, recovery, mutation policy, anti-patterns.

### Шаг 5: Formal skill QA

Превратить [tests/test_skill_docs.py](../tests/test_skill_docs.py) в mini-conformance suite для agent-facing артефактов, а не только в проверку совпадения команд.

### Шаг 6: Repo-wide agent surfaces

Добавить отдельный слой для developer/reviewer workflows и минимальную cross-tool repo-wide instruction surface по модели AGENTS.

### Шаг 7: Provider extension workflow

Сделать путь добавления провайдера формальным: какие файлы менять, какие инварианты соблюдать, какие тесты обязательны, какие docs обновлять.

### Шаг 8: Cross-harness minimum support

Честно выбрать universal-first набор вместо попытки тащить editor-specific ветки. Минимальный practical набор: один основной skill и `AGENTS.md` как shared repo layer.

### Шаг 9: Governance layer

Добавить ownership, freshness policy, changelog/versioning и review cadence для skill pack и companion docs.

### Шаг 10: Eval scenarios

Подготовить golden scenarios по filename parsing, provider selection, dry-run behavior, transcript generation и handling пустых/неполных результатов.

### Шаг 11: Skill creator extraction

Только теперь имеет смысл пробовать извлекать creator. Он должен генерировать не просто markdown, а installable, testable и versioned outputs.

### Шаг 12: One-source multi-target R&D

Если предыдущие слои стабилизированы, можно исследовать MDA-подобный подход с одной source model и несколькими генерируемыми instruction targets.

### Шаг 13: Работа через superpowers-цикл

Исполнять изменения итерациями: discovery, implement, verify, docs parity. Один агент собирает локальный контекст, второй делает reliability review, третий проверяет instruction drift и acceptance gates.

### Шаг 14: Жесткий порядок приоритетов

- P0: runtime hardening, observability, skill QA.
- P1: skill pack productization, repo-wide agent surfaces, provider extension kit.
- P2: cross-harness packaging, governance/evals, creator extraction, one-source multi-target experiments.

Если поменять порядок, репозиторий начнет масштабировать недооформленную knowledge surface.

### Шаг 15: Жесткое определение успеха

Успех измеряется не количеством skill-файлов.

Успех означает следующее:

- оператор безопасно запускает workflow без археологии в коде,
- разработчик добавляет provider без поиска скрытых контрактов,
- docs не расходятся с кодом,
- agent-facing surfaces защищены тестами и ownership-правилами,
- cross-tool story честная и ограниченная,
- любой будущий skill creator строится на стабильных контрактах, а не маскирует их отсутствие.

## Порядок приоритетов

- P0: Фаза 1, Фаза 2, Фаза 4.
- P1: Фаза 3, Фаза 5, Фаза 6.
- P2: Фаза 7, Фаза 8, Фаза 9.

## Определение успеха

- Оператор может безопасно запускать workflow, не восстанавливая поведение системы по исходникам.
- Разработчик может добавлять или менять provider без повторного открытия скрытых контрактов.
- Agent-facing docs тестируются и версионируются, а не существуют на доверии.
- Cross-tool instruction support явно ограничен и поддерживается как инженерная поверхность.
- Любой будущий skill creator строится на стабильной knowledge model, а не на автоматически размноженной неоднозначности.

## Рекомендуемый ближайший шаг

Начинать не с нового генератора, а с узкого P0-пакета:

- runtime hardening вокруг transient failures и empty transcript outcomes,
- structured observability на уровне per-file outcome,
- расширение [tests/test_skill_docs.py](../tests/test_skill_docs.py) в полноценный skill conformance gate.

Это самый короткий путь от сильного специализированного репозитория к reference-grade vertical skill system.