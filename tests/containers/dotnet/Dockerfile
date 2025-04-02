# mcr.microsoft.com/dotnet/sdk:6.0.428-1-focal
FROM mcr.microsoft.com/dotnet/sdk@sha256:525ce79a6f545131df515ce34f7ee086eb18e4d707eff9676b2678f2f23b6d9e

WORKDIR /app/Fibonacci

ADD Fibonacci.cs /app/Fibonacci
RUN dotnet new console
RUN rm Program.cs
RUN dotnet publish -c Release -o .

CMD ["dotnet", "Fibonacci.dll"]