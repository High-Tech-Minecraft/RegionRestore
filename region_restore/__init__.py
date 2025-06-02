import prime_backup as pb
import threading, time, os, shutil
import json
from pathlib import Path

# state for current restore
restore_state = {'thread': None, 'abort': False}
# load plugin config
try:
    _config_path = Path(__file__).resolve().parent.parent / 'config.json'
    with open(_config_path) as _cf:
        config = json.load(_cf)
except Exception:
    config = {}
from mcdreforged.api.all import PluginServerInterface, CommandSource

def on_load(server: PluginServerInterface, old):
    def region_command(src: CommandSource, args):
        # prevent concurrent restores
        if restore_state['thread'] and restore_state['thread'].is_alive():
            src.reply('A restore is already in progress.')
            return
        # Usage: !!region <backup_id> <dimension> <region1> [region2] ...
        if len(args) < 3:
            src.reply('Usage: !!region <backup_id> <dimension> <region1> [region2] ...')
            return
        # parse backup id
        try:
            backup_id = int(args[0])
        except ValueError:
            src.reply(f'Invalid backup id: {args[0]}')
            return
        # parse dimension
        dim_arg = args[1].lower()
        dim_map = {'overworld': '', 'nether': 'DIM-1', 'end': 'DIM1'}
        if dim_arg not in dim_map:
            src.reply(f'Invalid dimension: {args[1]}. Choose overworld, nether, or end.')
            return
        dim_folder = dim_map[dim_arg]
        regions = args[2:]
        # start restore in background
        def do_restore():
            restore_state['abort'] = False
            # optional temporary backup before region restore
            if config.get('create_temp_backup', False):
                server.execute('say Creating temporary backup before restore...')
                try:
                    temp_id = pb.create_backup()
                    server.execute(f'say Temporary backup created with id {temp_id}')
                except Exception as e:
                    server.execute(f'say Temporary backup creation failed: {e}')
                    return
            # countdown before stopping server
            for i in range(10, 0, -1):
                if restore_state['abort']:
                    server.execute('say Region restore aborted.')
                    return
                server.execute(f'say Stopping server in {i} seconds...')
                time.sleep(1)
            # stop server
            server.execute('stop')
            # export backup into a unique folder to avoid collisions
            world_dir = os.getcwd()
            try:
                raw_export = pb.export_backup(backup_id)
                # prepare exports root
                export_root = os.path.join(world_dir, 'rr_exports')
                os.makedirs(export_root, exist_ok=True)
                # fetch backup description if available
                try:
                    meta = pb.get_backup(backup_id)
                    desc = meta.get('description', '')
                except Exception:
                    desc = ''
                safe_desc = ''.join(c if c.isalnum() else '_' for c in desc).strip('_')
                export_folder = f"{backup_id}_{safe_desc or 'backup'}"
                export_path = os.path.join(export_root, export_folder)
                # ensure unique folder
                if os.path.exists(export_path):
                    shutil.rmtree(export_path)
                shutil.move(raw_export, export_path)
            except Exception as e:
                server.execute(f'say Export failed: {e}')
                return
            # choose correct region folder per dimension
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
            # report results
            success = [r for r in regions if r not in failed]
            if success:
                server.execute(f'say Restored regions: {", ".join(success)} from backup {backup_id}')
            if failed:
                server.execute(f'say Failed to restore regions: {", ".join(failed)}')
        # launch thread
        thread = threading.Thread(target=do_restore, daemon=True)
        restore_state['thread'] = thread
        thread.start()
        src.reply(f'Scheduled restore of backup {backup_id} for regions: {", ".join(regions)}')
    # root prefix command for this plugin (lists subcommands)
    def rr_command(src: CommandSource, args):
        if not args:
            src.reply("RegionRestore commands:\n"
                       "!!rr restore <backup_id> <dimension> <region1> [region2] ... - restore regions\n"
                       "!!rr abort - cancel pending restore countdown")
            return
        sub = args[0].lower()
        if sub == 'restore':
            # delegate to region_command
            region_command(src, args[1:])
        elif sub == 'abort':
            if restore_state['thread'] and restore_state['thread'].is_alive():
                restore_state['abort'] = True
                src.reply('Region restore abort requested.')
            else:
                src.reply('No restore in progress to abort.')
        else:
            src.reply(f"Unknown subcommand: {sub}")
    # register commands
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
