import os
import queue
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path


class BotManager:
    """Gerencia a execução em background do processo src.main do bot."""

    def __init__(self):
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._listeners: list[queue.Queue] = []
        self._history = deque(maxlen=1000)

    def is_running(self) -> bool:
        """Verifica se o processo DO GERENCIADOR está rodando."""
        with self._lock:
            if self._process is None:
                return False
            return self._process.poll() is None

    def check_external_process(self) -> bool:
        """Verifica se há instâncias do bot rodando FORA deste painel."""
        try:
            # Comando Windows WMI para listar linhas de comando ativas
            res = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            # Se o bot foi iniciado por aqui, ele deve ignorar sua própria instância?
            # Na verdade, basta ver se a string "src.main" está lá. Se is_running() é False
            # mas o processo é encontrado pelo WMI, então está rodando externamente.
            output = res.stdout.lower()
            if "src.main" in output and "python" in output:
                return True
            return False
        except Exception:
            return False

    def start(self) -> tuple[bool, str]:
        """Inicia o processo do bot (src.main)."""
        with self._lock:
            if self.is_running():
                return False, "Bot já está rodando por este painel."
            
            if self.check_external_process():
                return False, "O bot já está rodando em outro terminal. Feche a janela externa primeiro."

            project_root = Path(__file__).resolve().parents[3]
            python_executable = sys.executable

            env = os.environ.copy()
            # Forçar output unbuffered para envio em tempo real via SSE
            env["PYTHONUNBUFFERED"] = "1"

            try:
                self._process = subprocess.Popen(
                    [python_executable, "-m", "src.main"],
                    cwd=str(project_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # Redireciona stderr para stdout
                    text=True,
                    env=env,
                    bufsize=1,  # Line-buffered
                )
            except Exception as e:
                return False, f"Falha ao iniciar processo: {e}"

            self._thread = threading.Thread(target=self._read_output, daemon=True)
            self._thread.start()
            self._broadcast("--- MÓDULO DASHBOARD DEU START NO BOT ---")
            return True, "Bot iniciado."

    def stop(self) -> tuple[bool, str]:
        """Interrompe o processo do bot."""
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                return False, "Bot não está rodando."

            # Envia SIGTERM
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Se não fechar em 5s, força SIGKILL
                self._process.kill()

            self._broadcast("--- MÓDULO DASHBOARD PAROU O BOT ---")
            self._process = None
            return True, "Bot interrompido."

    def subscribe_logs(self) -> queue.Queue:
        """Adiciona um listener para a stream de logs."""
        q = queue.Queue(maxsize=1000)
        # Envia histórico imediatamente
        for line in self._history:
            q.put(line)
        self._listeners.append(q)
        return q

    def unsubscribe_logs(self, q: queue.Queue) -> None:
        """Remove o listener da stream de logs."""
        if q in self._listeners:
            self._listeners.remove(q)

    def _broadcast(self, line: str) -> None:
        """Envia uma linha de log para todos os listeners."""
        self._history.append(line)
        for q in self._listeners.copy():
            try:
                q.put_nowait(line)
            except queue.Full:
                # Se a fila estiver cheia, ignora (evita travar a leitura no backend)
                pass

    def _read_output(self) -> None:
        """Lê a saída do subprocesso em loop."""
        if not self._process or not self._process.stdout:
            return

        for line in iter(self._process.stdout.readline, ""):
            # readline retorna vazio "" quando o processo finaliza
            if not line:
                break
            self._broadcast(line.rstrip())

        self._broadcast("--- PROCESSO DO BOT FINALIZADO ---")

# Instância Singleton global para uso da interface Flask
bot_manager = BotManager()
