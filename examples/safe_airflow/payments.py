from airflow.decorators import dag, task


@dag(schedule="@daily", max_active_runs=1)
def payments():
    @task(retries=3)
    def load():
        sql = """
        MERGE INTO analytics.payments AS target
        USING (
          SELECT * FROM raw.payments
          WHERE created_at >= '{{ data_interval_start }}'
            AND created_at < '{{ data_interval_end }}'
        ) AS source
        ON target.payment_id = source.payment_id
        WHEN MATCHED THEN UPDATE SET amount = source.amount
        WHEN NOT MATCHED THEN INSERT (payment_id, amount)
          VALUES (source.payment_id, source.amount)
        """
        warehouse.execute(sql)  # noqa: F821 - illustrative Airflow connection

    load()


payments()
