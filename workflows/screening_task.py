import asyncio
from skyvern import Skyvern

async def run_screening():
    skyvern = Skyvern.local()
    result = await skyvern.run_task(
        url="https://news.ycombinator.com/jobs",
        prompt="Find backend engineer roles and extract salary and qualifications",
        data_extraction_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "salary": {"type": "string"}
            }
        }
    )
    print(result)

if __name__ == "__main__":
    asyncio.run(run_screening())