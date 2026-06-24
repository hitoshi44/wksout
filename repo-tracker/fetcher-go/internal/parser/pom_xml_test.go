package parser

import "testing"

const pomSample = `<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>my-app</artifactId>
  <version>1.0.0</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
  </parent>
  <properties>
    <java.version>17</java.version>
    <maven.compiler.source>17</maven.compiler.source>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>5.10.0</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>com.example.lib</groupId>
      <artifactId>core</artifactId>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-compiler-plugin</artifactId>
        <version>3.11.0</version>
      </plugin>
    </plugins>
  </build>
</project>`

func TestPomCoordinates(t *testing.T) {
	p, err := ParsePomXml(pomSample)
	if err != nil {
		t.Fatal(err)
	}
	if p.GroupId == nil || *p.GroupId != "com.example" {
		t.Errorf("groupId %+v", p.GroupId)
	}
	if p.ArtifactId == nil || *p.ArtifactId != "my-app" {
		t.Errorf("artifactId %+v", p.ArtifactId)
	}
	if p.Version == nil || *p.Version != "1.0.0" {
		t.Errorf("version %+v", p.Version)
	}
}

func TestPomParent(t *testing.T) {
	p, _ := ParsePomXml(pomSample)
	if p.Parent == nil {
		t.Fatal("parent missing")
	}
	if p.Parent.GroupId != "org.springframework.boot" || p.Parent.ArtifactId != "spring-boot-starter-parent" || p.Parent.Version != "3.2.0" {
		t.Errorf("parent %+v", p.Parent)
	}
}

func TestPomPropertiesWithDotsInNames(t *testing.T) {
	p, _ := ParsePomXml(pomSample)
	if p.Properties["java.version"] != "17" {
		t.Errorf("java.version: %+v", p.Properties)
	}
	if p.Properties["maven.compiler.source"] != "17" {
		t.Errorf("maven.compiler.source: %+v", p.Properties)
	}
}

func TestPomDependenciesWithOptionalFields(t *testing.T) {
	p, _ := ParsePomXml(pomSample)
	if len(p.Dependencies) != 2 {
		t.Fatalf("len %d", len(p.Dependencies))
	}
	junit := p.Dependencies[0]
	if junit.GroupId != "org.junit.jupiter" || junit.ArtifactId != "junit-jupiter" {
		t.Errorf("junit %+v", junit)
	}
	if junit.Version == nil || *junit.Version != "5.10.0" {
		t.Errorf("junit version %+v", junit.Version)
	}
	if junit.Scope == nil || *junit.Scope != "test" {
		t.Errorf("junit scope %+v", junit.Scope)
	}
	core := p.Dependencies[1]
	if core.Version != nil || core.Scope != nil {
		t.Errorf("core should have no version/scope: %+v", core)
	}
}

func TestPomPlugins(t *testing.T) {
	p, _ := ParsePomXml(pomSample)
	if len(p.Plugins) != 1 {
		t.Fatalf("len %d", len(p.Plugins))
	}
	if p.Plugins[0].ArtifactId != "maven-compiler-plugin" {
		t.Errorf("plugin %+v", p.Plugins[0])
	}
	if p.Plugins[0].Version == nil || *p.Plugins[0].Version != "3.11.0" {
		t.Errorf("plugin version %+v", p.Plugins[0].Version)
	}
}

func TestPomIgnoresUnknownTopLevelElements(t *testing.T) {
	raw := `<project>
		<groupId>g</groupId>
		<artifactId>a</artifactId>
		<description>blah blah</description>
		<modules><module>m1</module></modules>
	</project>`
	p, err := ParsePomXml(raw)
	if err != nil {
		t.Fatal(err)
	}
	if *p.GroupId != "g" || *p.ArtifactId != "a" {
		t.Errorf("got %+v %+v", p.GroupId, p.ArtifactId)
	}
	if len(p.Properties) != 0 {
		t.Errorf("properties should be empty")
	}
}

func TestPomHandlesNamespacePrefix(t *testing.T) {
	raw := `<pom:project xmlns:pom="http://maven.apache.org/POM/4.0.0">
		<pom:groupId>g</pom:groupId>
		<pom:artifactId>a</pom:artifactId>
	</pom:project>`
	p, err := ParsePomXml(raw)
	if err != nil {
		t.Fatal(err)
	}
	if *p.GroupId != "g" || *p.ArtifactId != "a" {
		t.Errorf("got %+v %+v", p.GroupId, p.ArtifactId)
	}
}

func TestPomRejectsMalformed(t *testing.T) {
	if _, err := ParsePomXml(`<a>foo</b>`); err == nil {
		t.Fatal("expected error")
	}
}
