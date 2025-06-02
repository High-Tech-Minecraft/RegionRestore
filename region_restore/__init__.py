import threading, time, os, shutil, json
from pathlib import Path

# PrimeBackup imports
from prime_backup.action.create_backup_action import CreateBackupAction
from prime_backup.action.export_backup_action_directory import ExportBackupToDirectoryAction
from prime_backup.db.access import DbAccess
from prime_backup.operator import Operator

from mcdreforged.api.all import PluginServerInterface, CommandSource
from prime_backup.text import RText, RColor, click_and_run, mkcmd, TextComponents

# State for current restore
restore_state = {'thread': None, 'abort': False}

# Load plugin config
try:
    _config_path = Path(__file__).resolve().parent.parent / 'config.json'
    with open(_config_path) as _cf:
        config = json.load(_cf)
except Exception:
    config = {}

def on_load(server: PluginServerInterface, old):
    def region_command(src: CommandSource, args):
        if restore_state['thread'] and restore_state['thread'].is_alive():
            src.reply('A restore is already in progress.')
            return
        if len(args) < 3:
            src.reply('Usage: !!region <backup_id> <dimension> <region1> [region2] ...')
            return
        try:
            backup_id = int(args[0])
        except ValueError:
            src.reply(f'Invalid backup id: {args[0]}')
            return
        dim_arg = args[1].lower()
        dim_map = {'overworld': '', 'nether': 'DIM-1', 'end': 'DIM1'}
        if dim_arg not in dim_map:
            src.reply(f'Invalid dimension: {args[1]}. Choose overworld, nether, or end.')
            return
        dim_folder = dim_map[dim_arg]
        regions = args[2:]
        def do_restore():
            restore_state['abort'] = False
            # determine countdown duration (seconds)
            countdown_sec = config.get('restore_countdown_sec', 10)
            if config.get('create_temp_backup', False):
                server.broadcast(RText('!!! ', RColor.yellow) + RText('Creating temporary backup before restore...'))
                try:
                    temp_id = CreateBackupAction(
                        Operator.literal('RegionRestore'),
                        "Temporary backup before region restore"
                    ).run().id
                    server.broadcast(RText(f'Temporary backup created with id {temp_id}', RColor.green))
                except Exception as e:
                    server.broadcast(RText(f'Temporary backup creation failed: {e}', RColor.red))
                    return
            # countdown before stopping server
            for countdown in range(max(0, countdown_sec), 0, -1):
                if restore_state['abort']:
                    server.broadcast(RText('!!! ', RColor.red) + RText('Region restore aborted.'))
                    return
                # broadcast countdown with clickable abort command
                server.broadcast(click_and_run(
                    RText('!!! ', RColor.red) + RText(f'Stopping server in {countdown} seconds...'),
                    RText('Click to abort', RColor.green),
                    mkcmd('rr abort'),
                ))
                time.sleep(1)
            # stop the server
            server.execute('stop')
            world_dir = os.getcwd()
            try:
                export_root = os.path.join(world_dir, 'rr_exports')
                os.makedirs(export_root, exist_ok=True)
                # Export backup to a new folder
                export_folder = f"export_{backup_id}_{int(time.time())}"
                export_path = os.path.join(export_root, export_folder)
                ExportBackupToDirectoryAction(backup_id, export_path).run()
                # Fetch backup description (comment)
                try:
                    with DbAccess.open_session() as session:
                        meta = session.get_backup(backup_id)
                        desc = meta.comment if hasattr(meta, "comment") else ""
                except Exception:
                    desc = ""
                safe_desc = ''.join(c if c.isalnum() else '_' for c in desc).strip('_')
                if safe_desc:
                    final_export_path = os.path.join(export_root, f"{backup_id}_{safe_desc}")
                    if os.path.exists(final_export_path):
                        shutil.rmtree(final_export_path)
                    shutil.move(export_path, final_export_path)
                    export_path = final_export_path
            except Exception as e:
                server.broadcast(RText(f'Export failed: {e}', RColor.red))
                return
            if dim_folder:
                base_region_path = os.path.join(world_dir, dim_folder, 'region')
            else:
                base_region_path = os.path.join(world_dir, 'region')
            failed = []
            for region in regions:
                region_file = f'{region}.mca'
                src_file = os.path.join(export_path, region_file)
                os.makedirs(base_region_path, exist_ok=True)
                dest_file = os.path.join(base_region_path, region_file)
                try:
                    shutil.copy(src_file, dest_file)
                except Exception as e:
                    failed.append(region)
            success = [r for r in regions if r not in failed]
            if success:
                server.broadcast(RText(f"Restored regions: {', '.join(success)} from backup {backup_id}", RColor.green))
            if failed:
                server.broadcast(RText(f"Failed to restore regions: {', '.join(failed)}", RColor.red))
        thread = threading.Thread(target=do_restore, daemon=True)
        restore_state['thread'] = thread
        thread.start()
        src.reply(f'Scheduled restore of backup {backup_id} for regions: {", ".join(regions)}')

    def rr_command(src: CommandSource, args):
        if not args:
            src.reply("RegionRestore commands:\n"
                      "!!rr restore <backup_id> <dimension> <region1> [region2] ... - restore regions\n"
                      "!!rr abort - cancel pending restore countdown")
            return
        sub = args[0].lower()
        if sub == 'restore':
            region_command(src, args[1:])
        elif sub == 'abort':
            if restore_state['thread'] and restore_state['thread'].is_alive():
                restore_state['abort'] = True
                src.reply('Region restore abort requested.')
            else:
                src.reply('No restore in progress to abort.')
        else:
            src.reply(f"Unknown subcommand: {sub}")

    server.register_command(
        "rr",
        rr_command,
        "RegionRestore root command (use without args for help)"
    )
    server.register_command(
        "region",
        region_command,
        "Restore specific region files from a backup"
    )