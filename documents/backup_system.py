#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Система автоматического резервного копирования файлов
Использует Git для версионирования и создания резервных копий
"""

import os
import time
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set
import schedule
import git
from git import Repo, InvalidGitRepositoryError

class BackupSystem:
    """Основной класс системы резервного копирования"""
    
    def __init__(self, config_file: str = "backup_config.json"):
        self.config_file = config_file
        self.config = self.load_config()
        self.setup_logging()
        self.repo = None
        self.file_hashes = {}
        
    def load_config(self) -> Dict:
        """Загружает конфигурацию из файла или создает дефолтную"""
        default_config = {
            "directories": [
                "./documents",
                "./projects"
            ],
            "file_extensions": [".txt", ".py", ".js", ".html", ".css", ".md", ".json"],
            "backup_repo_path": "./backup_repo",
            "remote_url": "",  # Заполнить URL вашего GitHub репозитория
            "check_interval_minutes": 30,
            "max_file_size_mb": 50,
            "exclude_patterns": [".git", "__pycache__", "*.pyc", "node_modules", ".env"]
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # Объединяем с дефолтной конфигурацией
                    for key, value in default_config.items():
                        if key not in config:
                            config[key] = value
                    return config
            except Exception as e:
                print(f"Ошибка загрузки конфигурации: {e}")
                print("Используется конфигурация по умолчанию")
        
        # Создаем файл конфигурации
        with open(self.config_file, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        
        return default_config
    
    def setup_logging(self):
        """Настраивает логирование"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('backup.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def init_git_repo(self):
        """Инициализирует Git репозиторий"""
        repo_path = self.config["backup_repo_path"]
        
        try:
            # Пытаемся открыть существующий репозиторий
            self.repo = Repo(repo_path)
            self.logger.info(f"Использую существующий репозиторий: {repo_path}")
        except InvalidGitRepositoryError:
            # Создаем новый репозиторий
            os.makedirs(repo_path, exist_ok=True)
            self.repo = Repo.init(repo_path)
            self.logger.info(f"Создан новый Git репозиторий: {repo_path}")
            
            # Добавляем remote если указан
            if self.config["remote_url"]:
                try:
                    origin = self.repo.create_remote('origin', self.config["remote_url"])
                    self.logger.info(f"Добавлен remote: {self.config['remote_url']}")
                except Exception as e:
                    self.logger.warning(f"Не удалось добавить remote: {e}")
    
    def calculate_file_hash(self, file_path: str) -> str:
        """Вычисляет MD5 хеш файла"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            self.logger.error(f"Ошибка при вычислении хеша для {file_path}: {e}")
            return ""
    
    def should_exclude_file(self, file_path: str) -> bool:
        """Проверяет, нужно ли исключить файл из резервного копирования"""
        path_str = str(file_path).lower()
        
        # Проверяем паттерны исключения
        for pattern in self.config["exclude_patterns"]:
            if pattern.startswith("*."):
                # Расширение файла
                if path_str.endswith(pattern[1:]):
                    return True
            else:
                # Путь или имя папки
                if pattern in path_str:
                    return True
        
        # Проверяем размер файла
        try:
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            if file_size_mb > self.config["max_file_size_mb"]:
                return True
        except:
            pass
        
        return False
    
    def get_files_to_backup(self) -> List[str]:
        """Получает список файлов для резервного копирования"""
        files_to_backup = []
        
        for directory in self.config["directories"]:
            if not os.path.exists(directory):
                self.logger.warning(f"Директория не существует: {directory}")
                continue
            
            for root, dirs, files in os.walk(directory):
                for file in files:
                    file_path = os.path.join(root, file)
                    
                    # Проверяем исключения
                    if self.should_exclude_file(file_path):
                        continue
                    
                    # Проверяем расширение файла
                    if self.config["file_extensions"]:
                        file_ext = os.path.splitext(file)[1].lower()
                        if file_ext not in self.config["file_extensions"]:
                            continue
                    
                    files_to_backup.append(file_path)
        
        return files_to_backup
    
    def check_for_changes(self) -> Set[str]:
        """Проверяет файлы на изменения"""
        changed_files = set()
        current_files = self.get_files_to_backup()
        
        for file_path in current_files:
            try:
                current_hash = self.calculate_file_hash(file_path)
                if not current_hash:
                    continue
                
                # Проверяем, изменился ли файл
                if file_path not in self.file_hashes or self.file_hashes[file_path] != current_hash:
                    changed_files.add(file_path)
                    self.file_hashes[file_path] = current_hash
                    
            except Exception as e:
                self.logger.error(f"Ошибка при проверке файла {file_path}: {e}")
        
        return changed_files
    
    def copy_files_to_repo(self, changed_files: Set[str]):
        """Копирует измененные файлы в репозиторий"""
        repo_path = Path(self.config["backup_repo_path"])
        
        for file_path in changed_files:
            try:
                # Создаем относительный путь
                source_path = Path(file_path)
                relative_path = source_path.relative_to(Path.cwd()) if source_path.is_absolute() else source_path
                
                # Создаем целевой путь в репозитории
                target_path = repo_path / relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                
                # Копируем файл
                import shutil
                shutil.copy2(file_path, target_path)
                self.logger.info(f"Скопирован файл: {file_path} -> {target_path}")
                
            except Exception as e:
                self.logger.error(f"Ошибка при копировании файла {file_path}: {e}")
    
    def create_commit(self, changed_files: Set[str]):
        """Создает коммит с измененными файлами"""
        try:
            # Добавляем все файлы в индекс
            self.repo.git.add(A=True)
            
            # Создаем сообщение коммита
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_message = f"Автоматическое резервное копирование - {timestamp}\n\n"
            commit_message += f"Изменено файлов: {len(changed_files)}\n"
            
            if len(changed_files) <= 10:
                commit_message += "\nИзмененные файлы:\n"
                for file_path in sorted(changed_files):
                    commit_message += f"- {file_path}\n"
            
            # Создаем коммит
            self.repo.index.commit(commit_message)
            self.logger.info(f"Создан коммит с {len(changed_files)} файлами")
            
        except Exception as e:
            self.logger.error(f"Ошибка при создании коммита: {e}")
    
    def push_to_remote(self):
        """Отправляет изменения на удаленный репозиторий"""
        if not self.config["remote_url"]:
            self.logger.info("Remote URL не настроен, пропускаем push")
            return
        
        try:
            origin = self.repo.remote(name='origin')
            origin.push()
            self.logger.info("Изменения отправлены на удаленный репозиторий")
        except Exception as e:
            self.logger.error(f"Ошибка при отправке на удаленный репозиторий: {e}")
    
    def backup_cycle(self):
        """Выполняет один цикл резервного копирования"""
        self.logger.info("Начинаю проверку файлов...")
        
        try:
            changed_files = self.check_for_changes()
            
            if not changed_files:
                self.logger.info("Изменений не обнаружено")
                return
            
            self.logger.info(f"Обнаружено изменений: {len(changed_files)}")
            
            # Копируем файлы в репозиторий
            self.copy_files_to_repo(changed_files)
            
            # Создаем коммит
            self.create_commit(changed_files)
            
            # Отправляем на удаленный репозиторий
            self.push_to_remote()
            
            self.logger.info("Цикл резервного копирования завершен успешно")
            
        except Exception as e:
            self.logger.error(f"Ошибка в цикле резервного копирования: {e}")
    
    def run_scheduler(self):
        """Запускает планировщик для автоматического резервного копирования"""
        interval = self.config["check_interval_minutes"]
        schedule.every(interval).minutes.do(self.backup_cycle)
        
        self.logger.info(f"Планировщик запущен. Проверка каждые {interval} минут.")
        self.logger.info("Нажмите Ctrl+C для остановки")
        
        # Выполняем первую проверку сразу
        self.backup_cycle()
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)  # Проверяем каждую минуту
        except KeyboardInterrupt:
            self.logger.info("Остановка планировщика...")
    
    def run_once(self):
        """Выполняет резервное копирование один раз"""
        self.logger.info("Выполняю разовое резервное копирование...")
        self.backup_cycle()

def main():
    """Главная функция"""
    print("=== Система автоматического резервного копирования ===")
    print("1. Запустить автоматическое резервное копирование")
    print("2. Выполнить резервное копирование один раз")
    print("3. Выход")
    
    choice = input("Выберите опцию (1-3): ").strip()
    
    if choice in ['1', '2']:
        backup_system = BackupSystem()
        backup_system.init_git_repo()
        
        if choice == '1':
            backup_system.run_scheduler()
        else:
            backup_system.run_once()
    
    elif choice == '3':
        print("До свидания!")
    else:
        print("Неверный выбор!")

if __name__ == "__main__":
    main()