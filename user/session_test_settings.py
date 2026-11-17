"""Session regressions against an explicitly opted-in disposable local MongoDB.

Never imports production settings or uses configured application DB credentials.
Start the task-owned Mongo container on localhost:55433 before selecting this module.
"""
import os
from entity.test_settings import *  # noqa: F403

if os.environ.get('RUN_REALM_SESSION_TESTS') != '1':
    raise RuntimeError('Opt in explicitly to disposable realm-session tests.')
os.environ['AUTHENTICATION_SECRET'] = 'realm-session-tests-only-authentication-key'
os.environ['JWT_TOKEN'] = 'realm-session-tests-only-client-envelope-key'
from mongoengine import connect
connect(db='test_realm_sessions', host='mongodb://127.0.0.1:55433', serverSelectionTimeoutMS=3000, uuidRepresentation='standard')
