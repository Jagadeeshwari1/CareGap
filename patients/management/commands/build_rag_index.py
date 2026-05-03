"""
Management Command: build_rag_index
Usage: python manage.py build_rag_index

Embeds the clinical knowledge base into a FAISS vector index.
Must be run once before habit suggestions will work.

Requirements:
    pip install sentence-transformers faiss-cpu
"""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Build the FAISS vector index for the RAG pipeline'

    def handle(self, *args, **options):
        self.stdout.write('Building RAG FAISS index...')

        try:
            from rag.pipeline import rag_pipeline
            rag_pipeline.build_index()
            self.stdout.write(self.style.SUCCESS(
                '✓ FAISS index built successfully. '
                'Habit suggestions are now available.'
            ))
        except ImportError as e:
            self.stdout.write(self.style.ERROR(
                f'Missing dependencies: {e}\n'
                'Install with: pip install sentence-transformers faiss-cpu numpy'
            ))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Error: {e}'))
