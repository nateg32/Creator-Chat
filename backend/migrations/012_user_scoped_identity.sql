-- Normalize account identity and scope creator handles per user.

UPDATE users
SET email = lower(trim(email))
WHERE email IS NOT NULL AND email <> lower(trim(email));

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM users
    GROUP BY lower(trim(email))
    HAVING COUNT(*) > 1
  ) THEN
    RAISE EXCEPTION 'Cannot enforce case-insensitive unique emails until duplicate user emails are resolved.';
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_unique_idx
  ON users ((lower(email)));

UPDATE creators
SET handle = NULL
WHERE handle IS NOT NULL AND trim(handle) = '';

UPDATE creators
SET handle = lower(ltrim(trim(handle), '@'))
WHERE handle IS NOT NULL AND handle <> lower(ltrim(trim(handle), '@'));

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM creators
    WHERE handle IS NOT NULL
    GROUP BY user_id, handle
    HAVING COUNT(*) > 1
  ) THEN
    RAISE EXCEPTION 'Cannot enforce per-user creator handle uniqueness until duplicate handles within the same account are resolved.';
  END IF;
END $$;

ALTER TABLE creators DROP CONSTRAINT IF EXISTS creators_handle_key;
DROP INDEX IF EXISTS creators_handle_key;

CREATE UNIQUE INDEX IF NOT EXISTS creators_user_handle_unique_idx
  ON creators (user_id, handle)
  WHERE handle IS NOT NULL;