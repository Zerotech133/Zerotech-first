package com.qianjiangsoft.graphapi;

import com.qianjiangsoft.graphapi.service.ImportService;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.model.Vertex;
import com.qianjiangsoft.graphcore.service.*;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.ActiveProfiles;

import java.util.ArrayList;
import java.util.List;

@SpringBootTest
@ActiveProfiles("ut")
class GraphApiApplicationTests {

    @Autowired
    SpaceService spaceService;

    @Autowired
    GraphEngine graphEngine;

    @Autowired
    SchemaService schemaService;

    @Autowired
    VertexService vertexService;

    @Autowired
    EdgeService edgeService;

    @Autowired
    SpacePersistenceService spacePersistenceService;

    @Autowired
    SpaceSyncService spaceSyncService;

    @Autowired
    GraphAnalyseService graphAnalyseService;

    @Autowired
    ImportService importService;

    @Autowired
    GraphPathAnalyseService graphPathAnalyseService;


    @Test
    void contextLoads() {

    }


    @Test
    @BeforeEach
    void testInitGraphData(){

        //创建space
        Space space = spaceService.createSpace(null, "单元测试空间1");

        SpaceContextHolder.set(space);

        importService.importExampleData();
    }


    @Test
    void testSaveToDisk() {
        spacePersistenceService.saveData();
    }

    @Test
    void testFindPath(){
        Space space = SpaceContextHolder.get();
        Vertex vertex1 = space.getVertexList().get(0);
        Vertex vertex2 = space.getVertexList().get(1);

        List<EdgeType> edgeTypeList = schemaService.selectEdgeTypeListBySpaceId(space.getId());
        EdgeType edgeType = edgeTypeList.get(0);
        List<EdgeType> typeList = new ArrayList<>();
        typeList.add(edgeType);

//        List<List<Edge>> edge = graphPathAnalyseService.searchAllPath(vertex1, vertex2, typeList, 10, SearchPathDirection.undirected);
//        List<Edge> shortPath = graphPathAnalyseService.findShortestPath(vertex1, vertex2, typeList, 10, SearchPathDirection.undirected);
//        System.out.println(edge);

    }

    @AfterEach
    void clearSpace(){
        spaceService.deleteSpace(SpaceContextHolder.get().getId());
    }

}
