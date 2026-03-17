

IF OBJECT_ID('dbo.sessions', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.sessions (
    session_id UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
    user_label NVARCHAR(200) NULL,
    PRIMARY KEY (session_id)
  );
END;

IF OBJECT_ID('dbo.messages', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.messages (
    message_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    role NVARCHAR(50) NOT NULL,
    content NVARCHAR(MAX) NOT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_messages_sessions')
  BEGIN
    ALTER TABLE dbo.messages
    ADD CONSTRAINT fk_messages_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.summaries', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.summaries (
    session_id UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    summary_text NVARCHAR(MAX) NOT NULL,
    updated_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_summaries_sessions')
  BEGIN
    ALTER TABLE dbo.summaries
    ADD CONSTRAINT fk_summaries_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.evidence_items', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.evidence_items (
    evidence_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    kind NVARCHAR(50) NOT NULL,
    title NVARCHAR(300) NULL,
    org NVARCHAR(300) NULL,
    start_date NVARCHAR(40) NULL,
    end_date NVARCHAR(40) NULL,
    details NVARCHAR(MAX) NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_evidence_sessions')
  BEGIN
    ALTER TABLE dbo.evidence_items
    ADD CONSTRAINT fk_evidence_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;

IF OBJECT_ID('dbo.uploads', 'U') IS NULL
BEGIN
  CREATE TABLE dbo.uploads (
    upload_id BIGINT IDENTITY(1,1) PRIMARY KEY,
    session_id UNIQUEIDENTIFIER NOT NULL,
    stored_name NVARCHAR(500) NOT NULL,
    original_name NVARCHAR(500) NOT NULL,
    content_type NVARCHAR(120) NULL,
    size_bytes BIGINT NULL,
    created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
  );

  IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'fk_uploads_sessions')
  BEGIN
    ALTER TABLE dbo.uploads
    ADD CONSTRAINT fk_uploads_sessions
      FOREIGN KEY (session_id) REFERENCES dbo.sessions(session_id);
  END;
END;