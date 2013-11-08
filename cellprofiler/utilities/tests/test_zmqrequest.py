"""test_zmqrequest.py - test the zmqrequest framework

CellProfiler is distributed under the GNU General Public License.
See the accompanying file LICENSE for details.

Copyright (c) 2003-2009 Massachusetts Institute of Technology
Copyright (c) 2009-2013 Broad Institute
All rights reserved.

Please see the AUTHORS file for credits.

Website: http://www.cellprofiler.org
"""

import Queue
import threading
import tempfile
import zmq
import unittest
import uuid
import numpy as np

import cellprofiler.utilities.zmqrequest as Z

CLIENT_MESSAGE = "Hello, server"
SERVER_MESSAGE = "Hello, client"

class TestZMQRequest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zmq_context = zmq.Context()
        
    @classmethod
    def tearDownClass(cls):
        Z.join_to_the_boundary()
        cls.zmq_context.term()
        
    class ZMQClient(threading.Thread):
        '''A mockup of a ZMQ client to the boundary
        
        must be instantiated after an analysis has been started
        '''
        MSG_STOP = "STOP"
        MSG_SEND = "SEND"
        def __init__(self, analysis_id, name="Client thread"):
            threading.Thread.__init__(self, name = name)
            self.notify_addr = "inproc://" + uuid.uuid4().hex
            self.setDaemon(True)
            self.queue = Queue.Queue()
            self.response_queue = Queue.Queue()
            self.start_signal = threading.Semaphore(0)
            self.keep_going = True
            self.analysis_id = analysis_id
            self.start()
            self.start_signal.acquire()
            self.send_notify_socket = TestZMQRequest.zmq_context.socket(zmq.PUB)
            self.send_notify_socket.connect(self.notify_addr)
                
        def __enter__(self):
            return self
            
        def __exit__(self, type, value, traceback):
            self.stop()
            self.join()
            self.send_notify_socket.close()
            
        def run(self):
            self.work_socket = TestZMQRequest.zmq_context.socket(zmq.REQ)
            self.work_socket.connect(Z.the_boundary.request_address)
            self.notify_socket = TestZMQRequest.zmq_context.socket(zmq.SUB)
            self.notify_socket.setsockopt(zmq.SUBSCRIBE, '')
            self.notify_socket.bind(self.notify_addr)
            poller = zmq.Poller()
            poller.register(self.work_socket, zmq.POLLIN)
            poller.register(self.notify_socket, zmq.POLLIN)
            self.start_signal.release()
            try:
                while self.keep_going:
                    for sock, state in poller.poll():
                        if sock == self.work_socket:
                            rep = Z.Communicable.recv(self.work_socket)
                            self.response_queue.put((None, rep))
                        elif sock == self.notify_socket:
                            msg = self.notify_socket.recv()
                            if msg == self.MSG_STOP:
                                return
                            elif msg == self.MSG_SEND:
                                req = self.queue.get_nowait()
                                req.send_only(self.work_socket)
            except Exception, e:
                self.response_queue.put((e, None))
            finally:
                self.work_socket.close()
                self.notify_socket.close()
                
        def stop(self):
            self.keep_going = False
            self.send_notify_socket.send(self.MSG_STOP)
            
        def send(self, req):
            self.queue.put(req)
            self.send_notify_socket.send(self.MSG_SEND)
                
        def recv(self):
            exception, result = self.response_queue.get()
            if exception is not None:
                raise exception
            else:
                return result
        
    class ZMQServer(object):
        def __enter__(self):
            self.analysis_id = uuid.uuid4().hex
            self.upq = Queue.Queue()
            self.boundary = Z.register_analysis(self.analysis_id,
                                                self.upq)
            return self
            
        def recv(self, timeout):
            '''Receive a message'''
            try:
                req = self.upq.get(timeout)
                return req
            except Queue.Empty:
                raise AssertionError("Failed to receive message within timeout of %f sec" % timeout)
                    
        def __exit__(self, type, value, traceback):
            self.cancel()
            
        def cancel(self):
            if self.boundary is not None:
                self.boundary.cancel(self.analysis_id)
                self.boundary = None

    def test_01_01_start(self):
        with self.ZMQServer() as server:
            pass
        
    def test_01_02_send_and_receive(self):
        with self.ZMQServer() as server:
            with self.ZMQClient(server.analysis_id) as client:
                client.send(Z.AnalysisRequest(server.analysis_id,
                                              msg=CLIENT_MESSAGE))
                req = server.recv(10.)
                self.assertIsInstance(req, Z.AnalysisRequest)
                self.assertEqual(req.msg, CLIENT_MESSAGE)
                req.reply(Z.Reply(msg = SERVER_MESSAGE))
                response = client.recv()
                self.assertEqual(response.msg, SERVER_MESSAGE)
                
    def test_02_01_boundary_exit_after_send(self):
        with self.ZMQServer() as server:
            with self.ZMQClient(server.analysis_id) as client:
                client.send(Z.AnalysisRequest(server.analysis_id,
                                              msg=CLIENT_MESSAGE))
                req = server.recv(10.)
                self.assertIsInstance(req, Z.AnalysisRequest)
                self.assertEqual(req.msg, CLIENT_MESSAGE)
                server.cancel()
                req.reply(Z.Reply(msg = SERVER_MESSAGE))
                response = client.recv()
                self.assertIsInstance(response, Z.BoundaryExited)

    def test_02_02_boundary_exit_before_send(self):
        with self.ZMQServer() as server:
            with self.ZMQClient(server.analysis_id) as client:
                server.cancel()
                client.send(Z.AnalysisRequest(server.analysis_id,
                                              msg=CLIENT_MESSAGE))
                response = client.recv()
                self.assertIsInstance(response, Z.BoundaryExited)
                
    def test_03_01_announce_nothing(self):
        boundary = Z.start_boundary()
        socket = self.zmq_context.socket(zmq.SUB)
        socket.connect(boundary.announce_address)
        socket.setsockopt(zmq.SUBSCRIBE, '')
        obj = socket.recv_json()
        self.assertEqual(len(obj), 0)
        
    def test_03_02_announce_something(self):
        boundary = Z.start_boundary()
        with self.ZMQServer() as server:
            socket = self.zmq_context.socket(zmq.SUB)
            socket.connect(boundary.announce_address)
            socket.setsockopt(zmq.SUBSCRIBE, '')
            obj = socket.recv_json()
            self.assertEqual(len(obj), 1)
            self.assertEqual(len(obj[0]), 2)
            analysis_id, address = obj[0]
            self.assertEqual(address, Z.the_boundary.request_address)
            self.assertEqual(analysis_id, server.analysis_id)
        #
        # 
        req_socket = self.zmq_context.socket(zmq.REQ)
        req_socket.connect(address)
        
        #
        # The analysis should be gone immediately after the
        # server has shut down
        #
        obj = socket.recv_json()
        self.assertEqual(len(obj), 0)
        
    def test_03_03_test_lock_file(self):
        t = tempfile.NamedTemporaryFile()
        self.assertTrue(Z.lock_file(t.name))
        self.assertFalse(Z.lock_file(t.name))
        Z.unlock_file(t.name)
        self.assertTrue(Z.lock_file(t.name))
        Z.unlock_file(t.name)
        
    def test_03_04_json_encode(self):
        r = np.random.RandomState()
        r.seed(15)
        test_cases = [
            { "k":"v" },
            { "k":(1, 2, 3) },
            { (1, 2, 3): "k" },
            { 1: { u"k":"v" } },
            { "k": [ { 1:2 }, { 3:4}] },
            { "k": ( (1, 2 ,{ "k1":"v1" }), )},
            { "k": r.uniform(size=(5, 8)) },
            { "k": r.uniform(size=(7, 3)) > .5 }
        ]
        for test_case in test_cases:
            json_string, buf = Z.json_encode(test_case)
            result = Z.json_decode(json_string, buf)
            self.same(test_case, result)
            
    def test_03_05_json_encode_uint64(self):
        for dtype in np.uint64, np.int64, np.uint32:
            json_string, buf = Z.json_encode(
                dict(foo = np.arange(10).astype(dtype)))
            result = Z.json_decode(json_string, buf)
            self.assertEqual(result["foo"].dtype, np.int32)
            
        json_string, buf = Z.json_encode(
            dict(foo=np.arange(10).astype(np.int16)))
        result = Z.json_decode(json_string, buf)
        self.assertEqual(result["foo"].dtype, np.int16)
    
    def same(self, a, b):
        if isinstance(a, (float, int)):
            self.assertAlmostEquals(a, b)
        elif isinstance(a, basestring):
            self.assertEquals(a, b)
        elif isinstance(a, dict):
            self.assertTrue(isinstance(b, dict))
            for k in a:
                self.assertTrue(k in b)
                self.same(a[k], b[k])
        elif isinstance(a, (list, tuple)):
            self.assertEqual(len(a), len(b))
            for aa, bb in zip(a, b):
                self.same(aa, bb)
        elif not np.isscalar(a):
            np.testing.assert_almost_equal(a, b)
        else:
            self.assertEqual(a, b)
            
                