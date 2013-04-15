# Copyright 2012 10gen, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test Motor, an asynchronous driver for MongoDB and Tornado."""

import datetime
import unittest

import pymongo.database
from pymongo.errors import OperationFailure, CollectionInvalid
from pymongo.son_manipulator import AutoReference, NamespaceInjector
from tornado import gen, ioloop

import motor
from test import host, port, MotorTest, async_test_engine


class MotorDatabaseTest(MotorTest):
    @async_test_engine()
    def test_database(self, done):
        # Test that we can create a db directly, not just from MotorClient's
        # accessors
        cx = self.motor_client(host, port)
        db = motor.MotorDatabase(cx, 'pymongo_test')

        # Make sure we got the right DB and it can do an operation
        doc = yield motor.Op(db.test_collection.find_one, {'_id': 1})
        self.assertEqual(hex(1), doc['s'])
        done()

    def test_collection_named_delegate(self):
        db = self.motor_client(host, port).pymongo_test
        self.assertTrue(isinstance(db.delegate, pymongo.database.Database))
        self.assertTrue(isinstance(db['delegate'], motor.MotorCollection))

    @async_test_engine()
    def test_database_callbacks(self, done):
        db = self.motor_client(host, port).pymongo_test
        yield motor.Op(
            self.check_optional_callback, db.drop_collection, 'c')

        # check_optional_callback would call create_collection twice, and the
        # second call would raise "already exists", so test manually.
        self.assertRaises(TypeError, db.create_collection, 'c', callback='foo')
        self.assertRaises(TypeError, db.create_collection, 'c', callback=1)
        
        # No error without callback
        db.create_collection('c', callback=None)
        
        # Wait for create_collection to complete
        loop = ioloop.IOLoop.instance()
        for _ in range(10):
            yield gen.Task(loop.add_timeout, datetime.timedelta(seconds=0.5))
            if 'c' in (yield motor.Op(db.collection_names)):
                break

        yield motor.Op(
            self.check_required_callback, db.validate_collection, 'c')

        done()

    @async_test_engine()
    def test_command(self, done):
        cx = self.motor_client(host, port)
        result = yield motor.Op(cx.admin.command, "buildinfo")
        self.assertEqual(int, type(result['bits']))
        done()

    @async_test_engine()
    def test_create_collection(self, done):
        # Test creating collection, return val is wrapped in MotorCollection,
        # creating it again raises CollectionInvalid.
        db = self.motor_client(host, port).pymongo_test
        yield motor.Op(db.drop_collection, 'test_collection2')
        collection = yield motor.Op(db.create_collection, 'test_collection2')
        self.assertTrue(isinstance(collection, motor.MotorCollection))
        self.assertTrue(
            'test_collection2' in (yield motor.Op(db.collection_names)))

        with self.assertRaises(CollectionInvalid):
            yield motor.Op(db.create_collection, 'test_collection2')

        yield motor.Op(db.drop_collection, 'test_collection2')

        # Test creating capped collection
        collection = yield motor.Op(
            db.create_collection, 'test_capped', capped=True, size=1000)
        self.assertTrue(isinstance(collection, motor.MotorCollection))
        self.assertEqual(
            {"capped": True, 'size': 1000},
            (yield motor.Op(db.test_capped.options)))
        yield motor.Op(db.drop_collection, 'test_capped')

        done()

    @async_test_engine()
    def test_command_callback(self, done):
        cx = self.motor_client(host, port)
        yield motor.Op(self.check_optional_callback, cx.admin.command, 'buildinfo', check=False)
        done()

    @async_test_engine()
    def test_auto_ref_and_deref(self, done):
        # Test same functionality as in PyMongo's test_database.py; the
        # implementation for Motor for async is a little complex so we test
        # that it works here, and we don't just rely on synchrotest
        # to cover it.
        cx = self.motor_client(host, port)
        db = cx.pymongo_test

        # We test a special hack where add_son_manipulator corrects our mistake
        # if we pass a MotorDatabase, instead of Database, to AutoReference.
        db.add_son_manipulator(AutoReference(db))
        db.add_son_manipulator(NamespaceInjector())

        a = {"hello": u"world"}
        b = {"test": a}
        c = {"another test": b}

        yield motor.Op(db.a.remove, {})
        yield motor.Op(db.b.remove, {})
        yield motor.Op(db.c.remove, {})
        yield motor.Op(db.a.save, a)
        yield motor.Op(db.b.save, b)
        yield motor.Op(db.c.save, c)
        a["hello"] = "mike"
        yield motor.Op(db.a.save, a)
        result_a = yield motor.Op(db.a.find_one)
        result_b = yield motor.Op(db.b.find_one)
        result_c = yield motor.Op(db.c.find_one)

        self.assertEqual(a, result_a)
        self.assertEqual(a, result_b["test"])
        self.assertEqual(a, result_c["another test"]["test"])
        self.assertEqual(b, result_b)
        self.assertEqual(b, result_c["another test"])
        self.assertEqual(c, result_c)

        done()

    @async_test_engine()
    def test_authenticate(self, done):
        cx = self.motor_client(host, port)
        db = cx.pymongo_test

        yield motor.Op(db.system.users.remove)
        yield motor.Op(db.add_user, "mike", "password")
        users = yield motor.Op(db.system.users.find().to_list, length=10)
        self.assertTrue("mike" in [u['user'] for u in users])

        # We need to authenticate many times at once to make sure that
        # Pool's start_request() is properly isolating operations
        for i in range(100):
            db.authenticate(
                "mike", "password", callback=(yield gen.Callback(i)))

        yield motor.WaitAllOps(range(100))

        # just make sure there are no exceptions here
        yield motor.Op(db.logout)
        yield motor.Op(db.remove_user, "mike")
        users = yield motor.Op(db.system.users.find().to_list, length=10)
        self.assertFalse("mike" in [u['user'] for u in users])
        done()

    @async_test_engine()
    def test_validate_collection(self, done):
        cx = self.motor_client(host, port)
        db = cx.pymongo_test

        with self.assertRaises(TypeError):
            yield motor.Op(db.validate_collection, 5)
        with self.assertRaises(TypeError):
            yield motor.Op(db.validate_collection, None)
        with self.assertRaises(OperationFailure):
            yield motor.Op(db.validate_collection, "test.doesnotexist")
        with self.assertRaises(OperationFailure):
            yield motor.Op(db.validate_collection, db.test.doesnotexist)

        yield motor.Op(db.test.save, {"dummy": u"object"})
        self.assertTrue((yield motor.Op(db.validate_collection, "test")))
        self.assertTrue((yield motor.Op(db.validate_collection, db.test)))
        done()


if __name__ == '__main__':
    unittest.main()
