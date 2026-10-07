package com.qianjiangsoft.graphcore.service.impl;

import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.map.MapUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.helper.GraphAnalyseHelper;
import com.qianjiangsoft.graphcore.helper.VertexHelper;
import com.qianjiangsoft.graphcore.mapper.GraphTagMapper;
import com.qianjiangsoft.graphcore.model.*;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import com.qianjiangsoft.graphcore.service.VertexService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * @author marshallwu
 * @date 2022/2/24 10:52
 */
@Service
public class VertexServiceImpl implements VertexService {

    @Autowired
    GraphTagMapper graphTagMapper;

    @Autowired
    SpaceSyncService spaceSyncService;


    @Override
    public Vertex createVertex(Tag tag, String id, String name, Map<String, Object> propertyList) {

        Space space = SpaceContextHolder.get();
        Vertex vertex = space.getVertexMap().get(id);


        if (vertex != null) {

            List<VertexPropertyCaseInfo> newCaseInfoList = VertexHelper.getCaseInfoListFromProperty(propertyList);

            //合并属性
            if(CollUtil.isNotEmpty(newCaseInfoList)) {

                //查询已经存在的caseInfo属性
                List<VertexPropertyCaseInfo> oldCaseInfoList = VertexHelper.getCaseInfoListFromProperty(vertex.getPropertyList());

                Map<String, VertexPropertyCaseInfo> caseInfoMap = oldCaseInfoList.stream().collect(Collectors.toMap(VertexPropertyCaseInfo::getCaseId,
                        caseInfo -> caseInfo));

                //查询新的case info
                VertexPropertyCaseInfo newCaseInfo = newCaseInfoList.get(0);
                if(!caseInfoMap.containsKey(newCaseInfo.getCaseId())) {
                    oldCaseInfoList.add(newCaseInfo);
                }
                //替换属性
                vertex.getPropertyList().put(Constants.VertexPropertyName.caseInfoList, JSONUtil.toJsonStr(oldCaseInfoList));
            }
        }else {
            vertex = new Vertex(id, name, propertyList, tag);
            space.getVertexMap().put(vertex.getId(), vertex);
            space.getVertexList().add(vertex);
            return vertex;
        }

        return vertex;
    }


    @Override
    public void removeVertex(String id) {
        Space space = SpaceContextHolder.get();

        //查找实体
        Vertex vertex = space.getVertexMap().get(id);

        if(vertex == null) {
            return;
        }

        //删除相关边
        space.getEdgeList().removeIf(edge -> edge.getSource().getId().equals(id) || edge.getDestination().getId().equals(id));

        //删除实体
        space.getVertexMap().remove(id);
        space.getVertexList().remove(vertex);
    }

    @Override
    public Vertex findVertexById(String id) {
        Space space = SpaceContextHolder.get();
        return space.getVertexMap().get(id);
    }

    @Override
    public Vertex randomSelectOneVertex() {
        Space space = SpaceContextHolder.get();
        int index = RandomUtil.getRandom().nextInt(space.getVertexList().size());
        return space.getVertexList().get(index);
    }

    @Override
    public List<Vertex> searchVertexListByName(String keyWord, String tagName) {
        Space space = SpaceContextHolder.get();
        List<Vertex> vertexList = space.getVertexList();
        List<Vertex> vertexListReturn = new ArrayList<>();

        for (Vertex vertex : vertexList) {
            if(GraphAnalyseHelper.checkVertexNameCondition(vertex, tagName, keyWord)) {
                vertexListReturn.add(vertex);
            }
        }
        return vertexListReturn;
    }

    @Override
    public void mergeVertex(String id1, String id2) {
        Space space = SpaceContextHolder.get();
        Vertex vertex1 = space.getVertexMap().get(id1);
        Vertex vertex2 = space.getVertexMap().get(id2);

        if(vertex1 == null || vertex2 == null) {
            return;
        }

        //默认合并到vertex1
        // 合并属性
        vertex2.getPropertyList().forEach((key, value) -> vertex1.getPropertyList().putIfAbsent(key, value));

        //修改连接
        for (Edge edge : space.getEdgeList()) {

            if(edge.getSource().getId().equals(vertex2.getId())) {
                edge.setSource(vertex1);
            }

            if(edge.getDestination().getId().equals(vertex2.getId())) {
                edge.setDestination(vertex1);
            }
        }

        //删除实体
        space.getVertexMap().remove(vertex2.getId());
        space.getVertexList().remove(vertex2);

    }

    @Override
    public void updateVertexProperty(String id, Map<String, Object> propertyList) {
        Space space = SpaceContextHolder.get();
        Vertex vertex = space.getVertexMap().get(id);

        if(vertex == null) {
            return;
        }

        vertex.setPropertyList(propertyList);
    }
}
