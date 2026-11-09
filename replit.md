# NoirKino Telegram Bot

Telegram orqali kino kodlari bilan video va fayllarni qidirib yuboradigan, to‘liq o‘zbekcha bot.

## Run & Operate

- `uv run python bot.py` — NoirKino Telegram botini ishga tushirish
- `TELEGRAM_BOT_TOKEN` — Replit Secret sifatida saqlanadi
- `ADMIN_ID` — botni boshqaradigan Telegram foydalanuvchisining raqamli IDsi
- Kino va foydalanuvchi ma’lumotlari `noirkino.sqlite3` SQLite bazasida saqlanadi
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Bot uchun Postgres yoki `DATABASE_URL` talab qilinmaydi

## Stack

- Python 3.13, aiogram 3, Telegram Bot API long polling
- Ma’lumotlar bazasi: SQLite

## Where things live

- `bot.py` — bot handlerlari, admin paneli, SQLite sxemasi va asosiy mantiq
- `pyproject.toml` va `uv.lock` — Python bog‘liqliklari

## Architecture decisions

- Telegram kino fayllari yuklab olinmaydi; Bot API `file_id` qiymati saqlanadi va qayta ishlatiladi.
- Token Replit Secret’da, admin ID esa alohida environment variable’da turadi.

## Product

- Kino kodi yoki nomi bo‘yicha qidiruv, kategoriya, mashhur/so‘nggi kinolar va reyting.
- Majburiy kanal obunasi, Oddiy/VIP/PREMIUM darajalari, bonus va referal tizimi.
- Admin paneli orqali kino, kanal, status, reklama, statistika va ommaviy xabarlarni boshqarish.

## User preferences

- Bot foydalanuvchilari uchun barcha matnlar o‘zbek tilida bo‘lishi kerak.

## Gotchas

- Obunani tekshirish uchun bot majburiy obuna kanallarida admin huquqiga ega bo‘lishi kerak.
- Broadcast hamma foydalanuvchiga yuboriladi va jo‘natishdan oldin admin tasdig‘ini talab qiladi.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
