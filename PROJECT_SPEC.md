Internal student performance tracker for a medical faculty.

Stack:
- Django
- PostgreSQL
- WSL2 Ubuntu for development
- Windows Codex app with WSL agent

Rules:
- No real student data in development
- No self-registration
- Audit every sensitive action
- Design for later Keycloak integration
- LMS integration comes later, not now

First milestone:
1. Create apps: students, results, imports, audits
2. Create models for Student, Batch, Module, Exam, ExamResult, ResultUpload, AuditEvent
3. Register them in Django admin
4. Add tests
5. Keep the UI minimal
