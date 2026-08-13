# TODO

- [x] настроить связь видео с задачами в crm planfix.
  В файле data/config.yml должна быть секция `call_booking`:
  - `webhook_url`
  - `authorization_token`
  - `threshold_minutes`
  - `disable_recognition`
  1. Поднять вебхук, который получает POST request: `{"body":{"start_time":"2023-08-11T07:00:00.000000Z","task_id":"851030","manager_email":"manager@example.com"},"client_ip":"44.214.195.64","headers":{"accept":"*/*","accept-encoding":"gzip,deflate","authorization":"Bearer asdfasdf"},"method":"POST","path":"/","query":{}}`, запрос проверяет Authorization token из конфига. В payload должны быть: `{"start_time":"2023-08-11T07:00:00.000000Z","task_id":"851030","manager_email":"manager@example.com"}`. На него будет приходить информация о предстоящем звонке: дата, email менеджера, id задачи. Нужно записывать эту информацию в файл.
  2. При обнаружении нового видео нужно сопоставлять folder email и дату созвона. Дата может отличаться на threshold_minutes, default: 15. Если нашлось совпадение, то нужно отправить task_id через webhook в planfix.
  3. Должна быть настройка, которая запрещает распознавать видео, которое не сопоставилось со звонком.
  4. Для связанных с задачами распознаваний нужно отправлять текст keypoints в planfix. Пример запроса в data/send-to-planfix-example.sh, отправится запрос в реальную crm, в тестовую задачу. Хорошо, если можно использовать существующую фичу вебхука, либо можно дописать новую, создать в конфиге `planfix_create_comment_webhook`.
