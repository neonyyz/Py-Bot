from discord.ext import commands

GUILD_ID = 876523812559159336

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="sync")
    async def sync_cmd(self, ctx):
        """Sync all slash commands to the test guild (admin only, !sync)."""
        if str(ctx.author.id) != "569616893095313408":
            await ctx.send("You are not authorized to use this command.")
            return
        await self.bot.wait_until_ready()
        test_guild = ctx.guild or self.bot.get_guild(GUILD_ID)
        await self.bot.tree.sync(guild=test_guild)
        cmds = await self.bot.tree.fetch_commands(guild=test_guild)
        print(f"Fetched commands from Discord: {[cmd.name for cmd in cmds]}")

    @commands.command(name="shutdown")
    async def shutdown_cmd(self, ctx):
        """Shut down the bot (admin only, !shutdown)."""
        if str(ctx.author.id) != "569616893095313408":
            await ctx.send("You are not authorized to use this command.")
            return
        await ctx.send("Shutting down...")
        await self.bot.close()

async def setup(bot):
    await bot.add_cog(Admin(bot))