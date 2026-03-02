# Gazer Heartbeat Checklist

## Always Check
- [ ] If there are any pending cron job failures, log a warning
- [ ] If system disk usage > 90%, alert the user
- [ ] If any critical error appeared in logs since last heartbeat, summarize it

## Business Hours (09:00-18:00)
- [ ] Check if there are any pending user messages that weren't responded to
- [ ] If a GitHub notification was received via webhook, summarize it

## Nighttime (23:00-08:00)
- HEARTBEAT_OK unless something urgent
