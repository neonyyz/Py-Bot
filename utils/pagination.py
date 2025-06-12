from discord import Embed, ui, Interaction, ButtonStyle

class PaginationView(ui.View):
    def __init__(self, pages, title, ephemeral=False):
        super().__init__(timeout=30)  # Set timeout to 30 seconds
        self.pages = pages
        self.title = title
        self.page = 0
        self.ephemeral = ephemeral
        self.message = None

    def update_buttons(self):
        self.clear_items()
        prev_disabled = self.page == 0
        next_disabled = self.page == len(self.pages) - 1
        self.add_item(self.previous)
        self.add_item(self.next)
        self.previous.disabled = prev_disabled
        self.next.disabled = next_disabled

    async def send_or_edit(self, interaction: Interaction):
        embed = Embed(
            title=f"{self.title} (Page {self.page+1}/{len(self.pages)})",
            description=self.pages[self.page]
        )
        self.update_buttons()
        if self.message is None:
            await interaction.response.send_message(embed=embed, view=self, ephemeral=self.ephemeral)
            self.message = await interaction.original_response()
        else:
            await self.message.edit(embed=embed, view=self)

    @ui.button(label="Previous", style=ButtonStyle.secondary, custom_id="prev", disabled=True)
    async def previous(self, interaction: Interaction, button: ui.Button):
        if self.page > 0:
            self.page -= 1
            await self.send_or_edit(interaction)
            await interaction.response.defer()

    @ui.button(label="Next", style=ButtonStyle.secondary, custom_id="next", disabled=True)
    async def next(self, interaction: Interaction, button: ui.Button):
        if self.page < len(self.pages) - 1:
            self.page += 1
            await self.send_or_edit(interaction)
            await interaction.response.defer()

    async def on_timeout(self):
        # Disable all buttons when the view times out
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

async def send_paginated_embed(interaction, items, title, per_page=10, ephemeral=False):
    if not items:
        embed = Embed(title=title, description="No items found.")
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        return
    pages = ["\n".join(items[i:i+per_page]) for i in range(0, len(items), per_page)]
    if len(pages) == 1:
        embed = Embed(title=title, description=pages[0])
        await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
    else:
        view = PaginationView(pages, title, ephemeral=ephemeral)
        await view.send_or_edit(interaction)