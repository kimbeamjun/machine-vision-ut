import asyncio
from app_settings.db_connection import AsyncSessionLocal
from database_tables.db_orm_models import CalibrationPointModel, SessionModel
from sqlalchemy.dialects.mysql import insert
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        try:
            # First insert a dummy session to satisfy foreign key
            new_session = SessionModel(status="uploaded")
            db.add(new_session)
            await db.commit()
            await db.refresh(new_session)
            session_id = new_session.id
            print(f"Created session {session_id}")

            stmt = insert(CalibrationPointModel).values(
                session_id=session_id,
                point_no=1,
                screen_x=0.1,
                screen_y=0.1,
                object_key="test_key"
            )
            stmt = stmt.on_duplicate_key_update(
                screen_x=stmt.inserted.screen_x,
                screen_y=stmt.inserted.screen_y,
                object_key=stmt.inserted.object_key
            )
            print("Executing statement...")
            await db.execute(stmt)
            await db.commit()
            print("Insert/Upsert successful")

            # Verify
            result = await db.execute(select(CalibrationPointModel).where(CalibrationPointModel.session_id == session_id))
            row = result.scalar_one_or_none()
            if row:
                print(f"Found row: point_no={row.point_no}, object_key={row.object_key}")
            else:
                print("Row not found after insert!")

        except Exception as e:
            print(f"Exception occurred: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
