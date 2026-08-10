from airflow.decorators import dag, task


@dag(schedule="@daily", max_active_runs=1)
def payments():
    @task(retries=3)
    def load():
        sql = """
        INSERT INTO analytics.payments
        SELECT *
        FROM raw.payments
        WHERE created_at::date = CURRENT_DATE
        """
        warehouse.execute(sql)  # noqa: F821 - illustrative Airflow connection

    load()


payments()
