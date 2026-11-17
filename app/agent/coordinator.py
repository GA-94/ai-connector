from app.agent.core import AgentCore

class Coordinator:
    def __init__(self, master: AgentCore, worker: AgentCore, log_callback=None):
        self.master = master
        self.worker = worker
        self.log = log_callback or (lambda msg: None)

    def run(self, messages: list, on_tool_log=None) -> str:
        return self.master.run(messages, on_tool_log=on_tool_log)